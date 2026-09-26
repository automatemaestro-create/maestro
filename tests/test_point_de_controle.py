"""Le point de contrôle des appels d'outil ferme par défaut (ticket #1304).

Deux failles silencieuses relevées le 2026-09-24 dans le point où Maestro
applique ses garde-fous aux outils d'un agent — le hook `PreToolUse` servi au
CLI (`maestro.providers.claude._hook_permissions`) et la frontière qu'il
consulte (`maestro.sandbox.en_place.FrontiereEcriture`) :

① **un appel que le point de contrôle ne sait pas nommer passait** : `{}`, donc
   « laisser passer », quand `tool_name` manquait. Les refus, les arbitrages, la
   frontière et la portée reposent tous sur ce nom : un CLI qui le déplacerait
   ouvrait tout sans un mot. Il est désormais **refusé, avec son motif** — et
   une entrée illisible aussi ;
② **`Grep` et `Glob` échappaient aux exclusions du périmètre** : `Grep` rend le
   contenu des fichiers, si bien qu'un `.env` que `Read` refuse se lisait par
   une recherche. Tout outil qui touche un chemin est confronté au périmètre, et
   un contenu exclu ne sort par **aucun** outil de lecture ou de recherche.

La sonde de démarrage — le second critère — a sa section en fin de module.

Aucun réseau, aucun modèle : le hook et la frontière se jouent en les appelant
comme le ferait le CLI, sur un projet jetable. Les tests sur le **vrai** CLI
portent le marqueur `cli_reel` et ne se jouent que sur demande (voir
`tests/conftest.py`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from maestro.agents.permissions import PolitiqueOutils
from maestro.projets.modele import Projet
from maestro.providers import claude as claude_mod
from maestro.sandbox import FrontiereEcriture


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` : sous Windows, `AppData` est une racine refusée.

    Même isolation que les tests de l'espace de travail (`tests/test_espace_projet.py`) :
    le `tmp_path` de pytest vit sous `AppData`, que `valider_racine` interdit.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


#: La valeur témoin d'un secret du projet : elle ne doit sortir par aucun outil.
SECRET = "sk-temoin-1304"


def _projet(tmp_path: Path) -> Projet:
    """Un projet non versionné plausible : du code, et les gisements que le périmètre retire."""
    racine = tmp_path / "projets" / "depensio"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("CLE = lire('CLE')\n", encoding="utf-8")
    (racine / "README.md").write_text("# Démo\n", encoding="utf-8")
    (racine / ".env").write_text(f"CLE={SECRET}\n", encoding="utf-8")
    (racine / "secrets").mkdir()
    (racine / "secrets" / "cle.pem").write_text(SECRET, encoding="utf-8")
    (racine / "node_modules" / "paquet").mkdir(parents=True)
    (racine / "node_modules" / "paquet" / "index.js").write_text("x", encoding="utf-8")
    (racine / "services" / "api").mkdir(parents=True)
    (racine / "services" / "api" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (racine / "services" / "api" / ".env").write_text(f"JETON={SECRET}\n", encoding="utf-8")
    (racine / "docs").mkdir()
    (racine / "docs" / "guide.md").write_text("Guide\n", encoding="utf-8")
    return Projet(id="prj-00001304", nom="Démo", racine=racine.as_posix())


def _frontiere(tmp_path: Path) -> tuple[Path, FrontiereEcriture]:
    projet = _projet(tmp_path)
    return Path(projet.racine), FrontiereEcriture.pour(projet.racine, projet.perimetre)


def _refus(sortie: object) -> str:
    """Le motif du `deny` rendu par le hook — échoue si l'appel n'est pas refusé."""
    assert isinstance(sortie, dict) and sortie, f"l'appel est passé : {sortie!r}"
    decision = sortie["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    return decision["permissionDecisionReason"]


# --------------------------------------------------------------------------- #
# ① Le point de contrôle ferme par défaut
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "entree",
    [
        {},
        {"tool_input": {"command": "ls"}},
        {"tool_name": "", "tool_input": {}},
        {"tool_name": "   ", "tool_input": {}},
        {"tool_name": None, "tool_input": {}},
        {"tool_name": 42, "tool_input": {}},
    ],
    ids=["vide", "sans-nom", "nom-vide", "nom-blanc", "nom-nul", "nom-nombre"],
)
def test_un_appel_sans_nom_lisible_est_refuse_avec_son_motif(entree: dict) -> None:
    traces: list[tuple[str, str]] = []
    hook = claude_mod._hook_permissions(
        PolitiqueOutils(deny=("Bash",)), lambda outil, motif: traces.append((outil, motif))
    )

    motif = _refus(asyncio.run(hook(entree, "tu-1", None)))

    assert "nom" in motif
    # Le refus est tracé comme les autres : c'est la ligne que le journal lira.
    assert traces and traces[0][1] == motif


def test_un_appel_sans_nom_est_refuse_meme_sans_politique(tmp_path: Path) -> None:
    # Le hook monté pour la seule frontière (#839) ferme aussi : sans nom, il n'y
    # a ni chemin à confronter ni outil à reconnaître.
    _, frontiere = _frontiere(tmp_path)
    hook = claude_mod._hook_permissions(None, None, frontiere=frontiere)

    _refus(asyncio.run(hook({"tool_input": {"file_path": ".env"}}, "tu-1", None)))


@pytest.mark.parametrize(
    "entree",
    [None, "cat .env", ["Read", ".env"], 7],
    ids=["absente", "texte", "liste", "nombre"],
)
def test_une_entree_illisible_est_refusee(entree: object) -> None:
    hook = claude_mod._hook_permissions(PolitiqueOutils(), None)
    appel: dict[str, object] = {"tool_name": "Read"}
    if entree is not None:
        appel["tool_input"] = entree

    motif = _refus(asyncio.run(hook(appel, "tu-1", None)))

    assert "Read" in motif and "entrée" in motif


def test_un_appel_lisible_et_permis_passe_toujours() -> None:
    # Fermer par défaut ne ferme que ce qu'on ne sait pas lire.
    hook = claude_mod._hook_permissions(PolitiqueOutils(deny=("Bash",)), None)
    assert asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "a"}}, "t", None)) == {}


# --------------------------------------------------------------------------- #
# ② La frontière : un outil qui touche un chemin est confronté au périmètre
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("outil", "entree"),
    [
        ("Read", {}),
        ("Read", {"file_path": ""}),
        ("Write", {"content": "x"}),
        ("Edit", {"file_path": 3}),
        ("NotebookEdit", {"notebook_path": None}),
        ("Grep", {"pattern": "CLE", "path": 3}),
        ("Glob", {"path": "src"}),
        ("Glob", {"pattern": ["*.py"]}),
        ("Read", "pas un objet"),
        ("Grep", None),
    ],
)
def test_un_outil_a_chemin_dont_l_entree_ne_se_lit_pas_est_refuse(
    tmp_path: Path, outil: str, entree: object
) -> None:
    # Deviner « passe » ici était le trou : un CLI qui renommerait l'argument de
    # chemin ouvrait la frontière entière sans un mot.
    _, frontiere = _frontiere(tmp_path)
    motif = frontiere.refus(outil, entree)
    assert motif is not None and "illisible" in motif, (outil, entree)


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": "CLE", "path": ".env"},
        {"pattern": "CLE", "path": "services/api/.env"},
        {"pattern": "BEGIN", "path": "secrets"},
        {"pattern": "BEGIN", "path": "secrets/cle.pem"},
        {"pattern": "x", "path": "node_modules/paquet"},
    ],
)
def test_une_recherche_dans_un_chemin_exclu_est_refusee(tmp_path: Path, entree: dict) -> None:
    _, frontiere = _frontiere(tmp_path)
    motif = frontiere.refus("Grep", entree)
    assert motif is not None and "exclu du périmètre" in motif, entree


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": "CLE"},
        {"pattern": "CLE", "path": ""},
        {"pattern": "CLE", "path": "."},
        {"pattern": "CLE", "path": "services"},
        {"pattern": "CLE", "path": "..", "glob": "*.env"},
    ],
    ids=["sans-chemin", "chemin-vide", "racine", "dossier-qui-contient", "au-dessus"],
)
def test_une_recherche_dont_la_portee_atteint_un_exclu_est_refusee(
    tmp_path: Path, entree: dict
) -> None:
    # Le cas de l'étape de reproduction : « cherche une clé dans le projet »,
    # un `Grep` sans chemin — il traverse la racine, `.env` compris.
    _, frontiere = _frontiere(tmp_path)

    motif = frontiere.refus("Grep", entree)

    assert motif is not None and "exclu" in motif, entree
    assert ".env" in motif


def test_le_refus_d_une_recherche_donne_ou_chercher(tmp_path: Path) -> None:
    # Une adresse plutôt qu'un refus sec : ce qui, sous la portée, ne contient rien d'exclu.
    _, frontiere = _frontiere(tmp_path)

    motif = frontiere.refus("Grep", {"pattern": "CLE"})

    assert motif is not None
    assert "`src`" in motif and "`docs`" in motif and "`README.md`" in motif
    # `services` porte un `.env` à sa racine : ce n'est pas une adresse.
    assert "`services`" not in motif


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": "CLE", "path": "src"},
        {"pattern": "CLE", "path": "src/app.py"},
        {"pattern": "main", "path": "services/api/main.py"},
        {"pattern": "Guide", "path": "docs", "glob": "*.md"},
    ],
)
def test_une_recherche_dans_le_perimetre_passe(tmp_path: Path, entree: dict) -> None:
    _, frontiere = _frontiere(tmp_path)
    assert frontiere.refus("Grep", entree) is None, entree


def test_une_recherche_hors_du_projet_n_est_pas_le_sujet_du_perimetre(tmp_path: Path) -> None:
    # Même règle que la lecture : le périmètre borne le projet, pas le poste.
    _, frontiere = _frontiere(tmp_path)
    ailleurs = tmp_path / "ailleurs"
    ailleurs.mkdir()
    (ailleurs / ".env").write_text("X=1\n", encoding="utf-8")
    assert frontiere.refus("Grep", {"pattern": "X", "path": str(ailleurs)}) is None


def test_une_recherche_qui_traverserait_un_lien_vers_un_exclu_est_refusee(tmp_path: Path) -> None:
    racine, frontiere = _frontiere(tmp_path)
    try:
        (racine / "docs" / "config").symlink_to(racine / ".env")
    except (OSError, NotImplementedError):  # pragma: no cover - dépend des droits Windows
        pytest.skip("liens symboliques indisponibles sur ce poste")

    motif = frontiere.refus("Grep", {"pattern": "CLE", "path": "docs"})

    assert motif is not None and "docs/config" in motif


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": ".env"},
        {"pattern": "services/api/.env"},
        {"pattern": "node_modules/**/*.js"},
        {"pattern": "secrets/*"},
        {"pattern": "*", "path": "secrets"},
        {"pattern": "**/*.js", "path": "node_modules"},
    ],
)
def test_une_liste_qui_vise_un_exclu_est_refusee(tmp_path: Path, entree: dict) -> None:
    _, frontiere = _frontiere(tmp_path)
    motif = frontiere.refus("Glob", entree)
    assert motif is not None and "exclu du périmètre" in motif, entree


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": "**/*.py"},
        {"pattern": "src/*.py"},
        {"pattern": "*.md", "path": "docs"},
    ],
)
def test_une_liste_de_noms_dans_le_perimetre_passe(tmp_path: Path, entree: dict) -> None:
    # `Glob` rend des **noms**, jamais un contenu : un motif qui traverse la
    # racine ne lit rien de ce que le périmètre retire. Ce qu'il vise en toutes
    # lettres, lui, est confronté (test précédent).
    _, frontiere = _frontiere(tmp_path)
    assert frontiere.refus("Glob", entree) is None, entree


@pytest.mark.parametrize(
    ("outil", "entree"),
    [
        ("Read", {"file_path": ".env"}),
        ("Read", {"file_path": "services/api/.env"}),
        ("NotebookRead", {"notebook_path": "secrets/cle.pem"}),
        ("Grep", {"pattern": "CLE"}),
        ("Grep", {"pattern": "CLE", "path": "."}),
        ("Grep", {"pattern": "CLE", "path": ".env"}),
        ("Grep", {"pattern": "JETON", "path": "services", "output_mode": "content"}),
        ("Grep", {"pattern": "sk-", "path": "secrets", "output_mode": "files_with_matches"}),
    ],
)
def test_un_contenu_exclu_ne_sort_par_aucun_outil_de_lecture_ou_de_recherche(
    tmp_path: Path, outil: str, entree: dict
) -> None:
    """Le critère, joué au point de contrôle tel que le CLI l'appelle.

    `files_with_matches` en fait partie : une recherche qui ne rend que des noms
    dit quand même si le motif est dans le fichier, et c'est un contenu.
    """
    _, frontiere = _frontiere(tmp_path)
    traces: list[tuple[str, str]] = []
    hook = claude_mod._hook_permissions(
        None, lambda o, m: traces.append((o, m)), frontiere=frontiere
    )

    motif = _refus(asyncio.run(hook({"tool_name": outil, "tool_input": entree}, "t", None)))

    assert SECRET not in motif
    assert traces == [(outil, motif)]
