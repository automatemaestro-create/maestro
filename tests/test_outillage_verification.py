"""Ce que Maestro écrit dans un projet est vérifié en l'exécutant (#1160).

Les deux critères du ticket, et ce qui les tient :

① **chaque commande d'outillage est jouée avant d'être écrite**, le manifeste garde
   son verdict — vérifiée, échouée avec sa sortie, ou à vérifier avec sa raison — et
   le rapport montré à la personne le dit commande par commande. Éprouvé à trois
   étages : le domaine (`maestro.outillage.verification`) avec un joueur doublé,
   pour chaque verdict et chaque raison ; la mécanique réelle
   (`maestro.sandbox.verification`) sous `@pytest.mark.commandes_jouees`, avec un
   vrai bash — la copie, la racine intacte, l'arrêt de la descendance, la branche
   d'un projet versionné qui ne porte rien de ce que les commandes produisent ;
② **sur une pile que les tables ne connaissent pas, l'outillage écrit porte les
   commandes du projet, vérifiées** — par la route même de l'écran, sur une solution
   .NET lue par le modèle (#1158) : `dotnet build` et `dotnet test` sont jouées et
   écrites avec leur verdict, aucune commande de table n'apparaît.

Sans `@pytest.mark.commandes_jouees`, `tests/conftest.py` retire l'interpréteur :
c'est ce qui laisse les quarante autres tests de génération sans rien jouer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.capacity import CapacityStore
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.projets import ServiceProjets
from maestro.outillage import (
    CHEMIN_MANIFESTE,
    DOSSIER_SKILLS,
    Commande,
    Constats,
    Entree,
    Gestionnaire,
    Piece,
    Recommandation,
    generer_outillage,
    recommander,
)
from maestro.outillage.verification import (
    A_VERIFIER,
    ECHOUEE,
    ETATS_VERIFICATION,
    VERIFIEE,
    Delais,
    Verificateur,
    Verification,
    commandes_ecrites,
)
from maestro.projets import ProjetStore
from maestro.projets.modele import Perimetre, Projet
from maestro.projets.perimetre import motifs_compiles
from maestro.projets.racine import detecter_vcs
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution

GIT = shutil.which("git")

#: L'interpréteur réel, relevé **avant** que la garde du conftest ne le retire.
BASH = execution.interprete()

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")
besoin_de_bash = pytest.mark.skipif(BASH is None, reason="aucun bash sur ce poste")

SOURCE = {
    "type": "analyse",
    "projet_id": "prj-0000beef",
    "reference": "ana-0000beef",
    "resume": "Python ; uv ; tests : pytest",
}
QUAND = "2026-09-24T12:00:00+00:00"

#: Un interpréteur factice pour les tests au joueur doublé : il n'est jamais lancé.
FAUX_BASH = ("bash", "-c")


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`Path.home()` sous `tmp_path` : `valider_racine` refuse `AppData` sous Windows (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))


# --------------------------------------------------------------------------- #
# Fabriques                                                                    #
# --------------------------------------------------------------------------- #


class _Joueur:
    """Un joueur doublé : il rend un résultat par commande et retient où il a joué."""

    def __init__(self, resultats: dict[str, execution.Execution] | None = None) -> None:
        self._resultats = resultats or {}
        self.joues: list[tuple[str, Path, float]] = []

    def __call__(
        self, commande: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        self.joues.append((commande, Path(cwd), delai_s))
        return self._resultats.get(commande, execution.Execution(code=0, sortie="ok", duree_s=0.1))


def _constats() -> Constats:
    """Un projet Python : installer, tester, vérifier le style, démarrer."""
    return Constats(
        gestionnaires=(Gestionnaire(nom="uv", chemin="pyproject.toml", installer="uv sync"),),
        commandes=(
            Commande(usage="installer", commande="uv sync", chemin="pyproject.toml"),
            Commande(usage="tester", commande="pytest", chemin="pyproject.toml"),
            Commande(usage="lint", commande="ruff check .", chemin="pyproject.toml"),
            Commande(usage="demarrer", commande="uv run python -m app", chemin="pyproject.toml"),
        ),
    )


def _racine(tmp_path: Path, *fichiers: str) -> Path:
    """Un projet non versionné portant `fichiers` (vides) — `pyproject.toml` par défaut."""
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    for relatif in fichiers or ("pyproject.toml",):
        (racine / relatif).parent.mkdir(parents=True, exist_ok=True)
        (racine / relatif).write_text("", encoding="utf-8")
    return racine


def _projet(racine: Path, **kwargs: Any) -> Projet:
    return Projet(id="prj-0000beef", nom="Dépensio", racine=racine.as_posix(), **kwargs)


def _generer(racine: Path, verificateur: Verificateur, constats: Constats | None = None) -> Any:
    constats = constats or _constats()
    return generer_outillage(
        _projet(racine),
        constats,
        recommander(constats),
        source=SOURCE,
        horodatage=QUAND,
        verificateur=verificateur,
    )


def _manifeste(racine: Path) -> dict[str, Any]:
    return json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))


def _ligne(texte: str, commande: str) -> str:
    """La ligne d'`AGENTS.md` qui écrit `commande`."""
    return next(ligne for ligne in texte.splitlines() if f"`{commande}`" in ligne)


# --------------------------------------------------------------------------- #
# ① Chaque commande est jouée avant d'être écrite, et son verdict la suit       #
# --------------------------------------------------------------------------- #


def test_chaque_commande_ecrite_est_jouee_dans_une_copie_avant_d_etre_ecrite(
    tmp_path: Path,
) -> None:
    racine = _racine(tmp_path)
    joueur = _Joueur()

    preparation = _generer(racine, Verificateur(joueur=joueur, interprete=FAUX_BASH))

    ecrites = [c.commande for c in commandes_ecrites(_constats(), recommander(_constats()))]
    assert [commande for commande, _, _ in joueur.joues] == ecrites
    # L'installation d'abord : sur un arbre frais, tout le reste en dépend.
    assert ecrites[0] == "uv sync"
    # Jamais dans la racine, ni dessous : dans une copie, retirée depuis.
    for _, cwd, _ in joueur.joues:
        assert not cwd.is_relative_to(racine)
        assert not cwd.exists()
    verdicts = preparation.rapport.verifications
    assert [v.commande for v in verdicts] == ecrites
    assert {v.etat for v in verdicts} == {VERIFIEE}
    agents = (racine / "AGENTS.md").read_text(encoding="utf-8")
    assert "**Vérifiée** quand Maestro a écrit cet outillage : elle a rendu la main sans" in (
        _ligne(agents, "pytest")
    )


def test_le_manifeste_et_le_rapport_gardent_le_verdict_de_chaque_commande(
    tmp_path: Path,
) -> None:
    racine = _racine(tmp_path)
    joueur = _Joueur({"pytest": execution.Execution(code=1, sortie="2 failed", duree_s=3.2)})

    preparation = _generer(racine, Verificateur(joueur=joueur, interprete=FAUX_BASH))

    gardes = _manifeste(racine)["verifications"]
    rendus = preparation.to_dict()["rapport"]["verifications"]
    assert gardes == rendus
    par_commande = {v["commande"]: v for v in gardes}
    assert par_commande["pytest"] == {
        "usage": "tester",
        "commande": "pytest",
        "etat": ECHOUEE,
        "raison": "elle a rendu la main en erreur (code 1)",
        "code": 1,
        "sortie": "2 failed",
        "duree_s": 3.2,
    }
    assert {v["etat"] for v in gardes} <= ETATS_VERIFICATION
    assert Verification.from_dict(par_commande["pytest"]).to_dict() == par_commande["pytest"]


def test_une_commande_qui_echoue_n_est_jamais_ecrite_comme_une_convention(
    tmp_path: Path,
) -> None:
    """Nommée avec son code, sa sortie renvoyée au manifeste — dans `AGENTS.md` et le skill."""
    racine = _racine(tmp_path)
    joueur = _Joueur({"pytest": execution.Execution(code=1, sortie="2 failed", duree_s=3.2)})

    _generer(racine, Verificateur(joueur=joueur, interprete=FAUX_BASH))

    ligne = _ligne((racine / "AGENTS.md").read_text(encoding="utf-8"), "pytest")
    assert "⚠ **Échouée** quand Maestro a écrit cet outillage : elle a rendu la main en " in ligne
    assert "(code 1). Ne la tiens pas pour acquise" in ligne
    assert f"`{CHEMIN_MANIFESTE}`" in ligne
    assert "**Vérifiée**" not in ligne
    skill = (racine / DOSSIER_SKILLS / "lancer-les-tests" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "## Ce que Maestro en a vérifié" in skill
    assert "- `pytest` — ⚠ **Échouée**" in skill


def test_un_projet_neuf_encore_vide_ecrit_ses_commandes_a_verifier_et_le_dit(
    tmp_path: Path,
) -> None:
    """Un projet neuf n'a rien à jouer : rien n'est joué, et chaque commande le dit."""
    racine = tmp_path / "projets" / "vitrine"
    racine.mkdir(parents=True)
    joueur = _Joueur()
    neuf = Constats(
        commandes=(
            Commande(usage="installer", commande="npm ci", chemin="package.json",
                     origine="convention"),
            Commande(usage="tester", commande="npx vitest run", chemin="package.json",
                     origine="convention"),
        )
    )

    preparation = _generer(racine, Verificateur(joueur=joueur, interprete=FAUX_BASH), neuf)

    assert joueur.joues == []
    assert {v.etat for v in preparation.rapport.verifications} == {A_VERIFIER}
    assert all("encore vide" in v.raison for v in preparation.rapport.verifications)
    agents = (racine / "AGENTS.md").read_text(encoding="utf-8")
    assert "**À vérifier** : le dossier du projet est encore vide" in _ligne(agents, "npm ci")


def test_une_commande_dont_le_fichier_n_existe_pas_encore_est_a_verifier(
    tmp_path: Path,
) -> None:
    racine = _racine(tmp_path, "README.md")
    joueur = _Joueur()

    preparation = _generer(racine, Verificateur(joueur=joueur, interprete=FAUX_BASH))

    assert joueur.joues == []
    raisons = {v.raison for v in preparation.rapport.verifications}
    assert raisons == {
        "`pyproject.toml` n'existe pas encore dans le projet ; elle sera jouée quand il "
        "existera, à la prochaine écriture de l'outillage"
    }


def test_une_commande_hors_de_la_portee_du_projet_n_est_pas_jouee(tmp_path: Path) -> None:
    """L'arbitrage des actes : ce qu'un agent n'aurait pas fait seul, Maestro non plus."""
    racine = _racine(tmp_path)
    joueur = _Joueur()
    constats = Constats(
        commandes=(
            Commande(usage="installer", commande="pip install -e .", chemin="pyproject.toml"),
            Commande(usage="tester", commande="pytest", chemin="pyproject.toml"),
            Commande(usage="lint", commande="ruff check ../voisin", chemin="pyproject.toml"),
        )
    )

    preparation = _generer(racine, Verificateur(joueur=joueur, interprete=FAUX_BASH), constats)

    assert [commande for commande, _, _ in joueur.joues] == ["pytest"]
    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    for commande in ("pip install -e .", "ruff check ../voisin"):
        assert verdicts[commande].etat == A_VERIFIER
        assert verdicts[commande].raison.startswith("Maestro ne la joue pas sans vous")
        # La copie est un chemin temporaire : il n'a rien à faire dans `AGENTS.md`.
        assert execution.PREFIXE_COPIE not in verdicts[commande].raison
    assert "installe hors du dossier du projet" in verdicts["pip install -e ."].raison


def test_sans_bash_rien_n_est_joue_et_chaque_commande_le_dit(tmp_path: Path) -> None:
    """Le vérificateur par défaut, sous la garde du conftest : le poste sans interpréteur."""
    racine = _racine(tmp_path)

    preparation = _generer(racine, Verificateur())

    verdicts = preparation.rapport.verifications
    assert verdicts and {v.etat for v in verdicts} == {A_VERIFIER}
    assert all(v.raison.startswith("aucun bash n'a été trouvé sur ce poste") for v in verdicts)


def test_un_demarrage_qui_tourne_encore_au_bout_de_sa_fenetre_est_verifie(
    tmp_path: Path,
) -> None:
    """Un serveur qui démarre ne rend pas la main : c'est sa réussite, pas un délai dépassé."""
    racine = _racine(tmp_path)
    tourne = execution.Execution(code=None, sortie="Listening", duree_s=15.0, expiree=True)
    joueur = _Joueur({"uv run python -m app": tourne, "pytest": tourne})

    preparation = _generer(
        racine,
        Verificateur(joueur=joueur, interprete=FAUX_BASH, delais=Delais(demarrage_s=15)),
    )

    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    assert verdicts["uv run python -m app"].etat == VERIFIEE
    assert "tournait encore au bout de 15 s" in verdicts["uv run python -m app"].raison
    assert verdicts["pytest"].etat == A_VERIFIER
    assert verdicts["pytest"].raison.endswith("sans verdict")
    delais = {commande: delai for commande, _, delai in joueur.joues}
    assert delais["uv run python -m app"] == 15


def test_le_temps_alloue_epuise_rend_les_commandes_suivantes_a_verifier(
    tmp_path: Path,
) -> None:
    racine = _racine(tmp_path)
    joueur = _Joueur()

    preparation = _generer(
        racine,
        Verificateur(joueur=joueur, interprete=FAUX_BASH, delais=Delais(total_s=0)),
    )

    assert joueur.joues == []
    assert all("temps alloué" in v.raison for v in preparation.rapport.verifications)


def test_regenerer_avec_les_memes_verdicts_ne_reecrit_rien(tmp_path: Path) -> None:
    """La raison d'un verdict est déterministe : l'empreinte identique reste atteignable."""
    racine = _racine(tmp_path)
    lent = execution.Execution(code=0, sortie="ok", duree_s=41.7)
    verificateur = Verificateur(joueur=_Joueur({"pytest": lent}), interprete=FAUX_BASH)
    _generer(racine, verificateur)

    seconde = _generer(
        racine, Verificateur(joueur=_Joueur(), interprete=FAUX_BASH)
    )

    assert {e.etat for e in seconde.rapport.ecritures} == {"inchange"}


def test_seules_les_commandes_que_la_redaction_ecrit_sont_jouees() -> None:
    """Sans `AGENTS.md` retenu, seules les commandes des skills écrits comptent."""
    constats = _constats()
    skill = next(e for e in recommander(constats).entrees if e.nom == "lancer-les-tests")
    deja_la = Entree(
        type="skill",
        nom="verifier-le-style",
        chemin=".claude/skills/verifier-le-style/SKILL.md",
        etat="deja-present",
        raison="le projet porte déjà ce skill",
        commandes=("ruff check .",),
    )
    reco = Recommandation(entrees=(skill, deja_la))

    assert [c.commande for c in commandes_ecrites(constats, reco)] == ["pytest"]
    declare = {".claude/skills/verifier-le-style/SKILL.md": "fichier"}
    assert [c.commande for c in commandes_ecrites(constats, reco, portees=declare)] == [
        "pytest",
        "ruff check .",
    ]


def test_le_script_d_un_skill_est_joue_sous_l_usage_de_son_skill() -> None:
    """`bash scripts/tests.sh` n'est pas un constat : il prend l'usage du skill qui l'appelle."""
    constats = Constats()
    skill = Entree(
        type="skill",
        nom="lancer-les-tests",
        chemin=f"{DOSSIER_SKILLS}/lancer-les-tests/SKILL.md",
        etat="a-generer",
        raison="…",
        justification=Piece(nom="tests.sh", chemin="scripts/tests.sh", role="tester"),
        commandes=("bash scripts/tests.sh",),
    )

    (ecrite,) = commandes_ecrites(constats, Recommandation(entrees=(skill,)))

    assert (ecrite.usage, ecrite.commande, ecrite.chemin) == (
        "tester",
        "bash scripts/tests.sh",
        "scripts/tests.sh",
    )


# --------------------------------------------------------------------------- #
# La mécanique réelle : une copie, un vrai bash, un arrêt de toute la descendance #
# --------------------------------------------------------------------------- #


def test_la_copie_suit_le_perimetre_sans_atelier_et_disparait_en_sortie(tmp_path: Path) -> None:
    racine = _racine(
        tmp_path,
        "src/app.py",
        ".env",
        "secrets/cle.txt",
        "node_modules/x/index.js",
        ".maestro/outillage/manifeste.json",
    )

    with execution.copie_de_verification(
        racine, exclus=motifs_compiles(Perimetre().exclus), hors=(".maestro",)
    ) as copie:
        copies = {p.relative_to(copie).as_posix() for p in copie.rglob("*") if p.is_file()}
        assert not copie.is_relative_to(racine)
        assert copie.parent.name.startswith(execution.PREFIXE_COPIE)

    assert copies == {"src/app.py"}
    assert not copie.parent.exists()


def test_une_copie_trop_grosse_est_refusee_avec_son_motif(tmp_path: Path) -> None:
    racine = _racine(tmp_path, "a.txt", "b.txt", "c.txt")

    with pytest.raises(execution.CopieImpossible) as refus:  # noqa: SIM117
        with execution.copie_de_verification(racine, fichiers_max=2):
            pass

    assert refus.value.motif == "trop-de-fichiers"


def test_une_copie_impossible_rend_chaque_commande_a_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    racine = _racine(tmp_path)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise execution.CopieImpossible("trop-de-fichiers", "le projet est trop gros")

    joueur = _Joueur()
    monkeypatch.setattr(execution, "copie_de_verification", refuse)

    preparation = _generer(racine, Verificateur(joueur=joueur, interprete=FAUX_BASH))

    assert joueur.joues == []
    assert all(
        v.raison == "la copie de vérification n'a pas pu être faite — le projet est trop gros"
        for v in preparation.rapport.verifications
    )


@pytest.mark.commandes_jouees
@besoin_de_bash
def test_une_vraie_commande_est_jouee_dans_la_copie_et_la_racine_reste_intacte(
    tmp_path: Path,
) -> None:
    racine = _racine(tmp_path, "pyproject.toml", ".env", "secrets/cle.txt")
    (racine / "scripts").mkdir()
    (racine / "scripts" / "tests.sh").write_text(
        "test ! -e .env && test ! -e secrets/cle.txt && echo produit > produit.txt "
        "&& echo 'tout passe'\n",
        encoding="utf-8",
        newline="\n",
    )
    (racine / "scripts" / "casse.sh").write_text(
        "echo boom >&2\nexit 3\n", encoding="utf-8", newline="\n"
    )
    constats = Constats(
        commandes=(
            Commande(usage="tester", commande="bash scripts/tests.sh", chemin="scripts/tests.sh"),
            Commande(usage="lint", commande="bash scripts/casse.sh", chemin="scripts/casse.sh"),
        )
    )
    avant = sorted(p.relative_to(racine).as_posix() for p in racine.rglob("*"))

    preparation = _generer(racine, Verificateur(), constats)

    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    assert verdicts["bash scripts/tests.sh"].etat == VERIFIEE, verdicts
    assert verdicts["bash scripts/tests.sh"].sortie == "tout passe"
    assert verdicts["bash scripts/casse.sh"].etat == ECHOUEE
    assert verdicts["bash scripts/casse.sh"].code == 3
    assert verdicts["bash scripts/casse.sh"].sortie == "boom"
    # Ce que les commandes ont produit est resté dans la copie.
    assert not (racine / "produit.txt").exists()
    ecrits = {e.chemin for e in preparation.rapport.ecritures} | {CHEMIN_MANIFESTE}
    apres = sorted(p.relative_to(racine).as_posix() for p in racine.rglob("*"))
    assert set(apres) - set(avant) <= ecrits | _dossiers_de(ecrits)


def _dossiers_de(chemins: set[str]) -> set[str]:
    """Les dossiers qui portent `chemins` — ce que l'écriture a dû créer pour eux."""
    return {"/".join(c.split("/")[:i]) for c in chemins for i in range(1, c.count("/") + 1)}


@pytest.mark.commandes_jouees
@besoin_de_bash
def test_une_commande_trop_longue_est_arretee_avec_sa_descendance(tmp_path: Path) -> None:
    """Un fils qui tient la sortie ouverte : seul l'arrêt de tout l'arbre rend la main vite."""
    assert BASH is not None
    debut = time.monotonic()

    resultat = execution.jouer("sleep 30 & sleep 30", tmp_path, interprete=BASH, delai_s=1)

    assert resultat.expiree and resultat.code is None
    assert time.monotonic() - debut < 5


@pytest.mark.commandes_jouees
@besoin_de_bash
def test_une_commande_qui_laisse_un_demon_rend_son_verdict_sans_l_attendre(
    tmp_path: Path,
) -> None:
    """Le cas d'un serveur de build : la commande a fini, son démon tient encore la sortie."""
    assert BASH is not None
    debut = time.monotonic()

    resultat = execution.jouer(
        "(sleep 20 &) ; echo fini ; exit 4", tmp_path, interprete=BASH, delai_s=15
    )

    assert (resultat.code, resultat.sortie, resultat.expiree) == (4, "fini", False)
    assert time.monotonic() - debut < 5


@pytest.mark.commandes_jouees
@besoin_de_bash
@besoin_de_git
def test_la_branche_d_un_projet_versionne_ne_porte_rien_de_ce_que_les_commandes_produisent(
    tmp_path: Path,
) -> None:
    racine = _racine(tmp_path, "pyproject.toml")
    (racine / "scripts").mkdir()
    (racine / "scripts" / "tests.sh").write_text(
        "echo produit > produit.txt\n", encoding="utf-8", newline="\n"
    )
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(racine, "add", "-A")
    _git(racine, "-c", "user.email=t@m", "-c", "user.name=T", "commit", "--quiet", "-m", "socle")
    constats = Constats(
        commandes=(
            Commande(usage="tester", commande="bash scripts/tests.sh", chemin="scripts/tests.sh"),
        )
    )

    preparation = generer_outillage(
        _projet(racine, vcs=detecter_vcs(racine)),
        constats,
        recommander(constats),
        source=SOURCE,
        horodatage=QUAND,
    )

    (verdict,) = preparation.rapport.verifications
    assert verdict.etat == VERIFIEE
    portes = _git(racine, "ls-tree", "-r", "--name-only", preparation.branche).split()
    assert "AGENTS.md" in portes
    assert "produit.txt" not in portes
    assert not (racine / "produit.txt").exists()


def _git(racine: Path, *arguments: str) -> str:
    resultat = subprocess.run(
        [GIT or "git", *arguments],
        cwd=racine,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert resultat.returncode == 0, f"git {' '.join(arguments)} : {resultat.stderr}"
    return resultat.stdout


# --------------------------------------------------------------------------- #
# ② Une pile que les tables ne connaissent pas, par la route de l'écran         #
# --------------------------------------------------------------------------- #

#: Ce que le modèle répond sur une solution .NET (#1158) — aucune table ne la connaît.
LECTURE_DOTNET: tuple[str, ...] = (
    "LIRE: Depensio.sln\nLIRE: tests/Api.Tests/Api.Tests.csproj",
    "GESTIONNAIRE: dotnet | Depensio.sln |\n"
    "COMMANDE: construire | Depensio.sln | convention | solution .NET | dotnet build\n"
    "COMMANDE: tester | tests/Api.Tests/Api.Tests.csproj | convention | xunit | dotnet test\n"
    "FIN",
)


class _Lecteur(ModelProvider):
    """Le modèle qui lit le projet : il rejoue la lecture .NET, sans réseau."""

    name = "lecteur-factice"

    def __init__(self) -> None:
        self._reponses = list(LECTURE_DOTNET)

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        effort: str | None = None,
    ) -> str:
        return self._reponses.pop(0) if len(self._reponses) > 1 else self._reponses[0]


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """L'app réelle, projets bornés à l'atelier, dépôts d'agents temporaires."""
    core = tmp_path / "core"
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(
            ProjetStore(tmp_path / "depot"), racines_exploration=(tmp_path / "atelier",)
        ),
        agents_store=AgentStore(core / "agents"),
        surcharges=SurchargeStore(core / "surcharges"),
        playbooks=PlaybookStore(core / "playbooks"),
        permissions=PermissionStore(core / "permissions"),
        mcp=McpStore(core / "mcp"),
        capacites=CapacityStore(core / "capacite"),
        lecteur_outillage=_Lecteur(),
    )
    with TestClient(app) as ouvert:
        yield ouvert


def _solution_dotnet(atelier: Path) -> Path:
    racine = atelier / "depensio-net"
    fichiers = {
        "Depensio.sln": 'Project("{FAE0}") = "Api", "src\\Api\\Api.csproj", "{1}"\n',
        "src/Api/Api.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web" />\n',
        "src/Api/Program.cs": "var app = WebApplication.Create();\n",
        "tests/Api.Tests/Api.Tests.csproj": '<PackageReference Include="xunit" />\n',
    }
    for chemin, contenu in fichiers.items():
        (racine / chemin).parent.mkdir(parents=True, exist_ok=True)
        (racine / chemin).write_text(contenu, encoding="utf-8")
    return racine


def test_une_pile_qu_aucune_table_ne_connait_porte_ses_commandes_verifiees(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critère ② par la route de l'écran : les commandes du projet, jouées, et leur verdict."""
    racine = _solution_dotnet(tmp_path / "atelier")
    joueur = _Joueur(
        {"dotnet test": execution.Execution(code=1, sortie="Échec : 2 tests", duree_s=9.0)}
    )
    monkeypatch.setattr(execution, "interprete", lambda: FAUX_BASH)
    monkeypatch.setattr(execution, "jouer", joueur)
    declare = client.post(
        "/api/projets", json={"nom": racine.name, "racine": str(racine), "origine": "existant"}
    )
    assert declare.status_code == 201, declare.text
    projet = declare.json()["id"]
    analyse = client.get(f"/api/projets/{projet}/outillage/analyse").json()
    retenus = [e["chemin"] for e in analyse["recommandation"]["entrees"]]

    reponse = client.post(f"/api/projets/{projet}/outillage/generation", json={"retenus": retenus})

    assert reponse.status_code == 200, reponse.text
    verifications = reponse.json()["rapport"]["verifications"]
    assert [(v["commande"], v["etat"]) for v in verifications] == [
        ("dotnet build", VERIFIEE),
        ("dotnet test", ECHOUEE),
    ]
    assert verifications[1]["sortie"] == "Échec : 2 tests"
    assert [commande for commande, _, _ in joueur.joues] == ["dotnet build", "dotnet test"]
    agents = (racine / "AGENTS.md").read_text(encoding="utf-8")
    assert "**Vérifiée**" in _ligne(agents, "dotnet build")
    assert "⚠ **Échouée**" in _ligne(agents, "dotnet test")
    # Aucune commande de table : ce projet n'est ni Python ni TypeScript.
    for table in ("uv sync", "npm ci", "ruff check .", "npx tsc --noEmit"):
        assert table not in agents
    assert _manifeste(racine)["verifications"] == verifications
