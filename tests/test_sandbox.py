"""Tests de l'isolation d'exécution : l'espace de travail jetable (ticket #4).

Couvre le critère « contexte d'exécution isolé » : un répertoire dédié par tâche,
nettoyé en sortie (même sur exception), et le recensement des fichiers produits
(contenu texte capturé, binaire/volumineux laissé à None).
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from maestro.sandbox import ProducedFile, Workspace, isolated_workspace
from maestro.sandbox.workspace import _MAX_CAPTURE_BYTES


def test_workspace_cree_un_repertoire_isole_et_le_nettoie():
    with isolated_workspace() as ws:
        chemin = ws.path
        assert chemin.is_dir()
        assert not any(chemin.iterdir())  # créé vide
    # Nettoyé à la sortie du contexte (keep=False par défaut).
    assert not chemin.exists()


def test_workspace_keep_conserve_le_repertoire():
    with isolated_workspace(keep=True) as ws:
        chemin = ws.path
    try:
        assert chemin.is_dir()  # conservé pour inspection
    finally:
        # Nettoyage manuel : keep=True nous en laisse la responsabilité.
        for f in chemin.rglob("*"):
            if f.is_file():
                f.unlink()
        chemin.rmdir()


def test_workspace_nettoye_meme_sur_exception():
    capture: dict[str, Path] = {}
    with pytest.raises(RuntimeError):
        with isolated_workspace() as ws:
            capture["path"] = ws.path
            (ws.path / "brouillon.txt").write_text("wip", encoding="utf-8")
            raise RuntimeError("boum")
    assert not capture["path"].exists()


def test_produced_files_recense_trie_et_capture_le_texte():
    with isolated_workspace() as ws:
        (ws.path / "b.txt").write_text("monde", encoding="utf-8")
        (ws.path / "a.py").write_text("print('salut')", encoding="utf-8")
        sous_dossier = ws.path / "src"
        sous_dossier.mkdir()
        (sous_dossier / "c.txt").write_text("imbriqué", encoding="utf-8")

        fichiers = ws.produced_files()

    chemins = [f.chemin for f in fichiers]
    # Tri déterministe par chemin relatif POSIX, répertoire non listé en tant que tel.
    assert chemins == ["a.py", "b.txt", "src/c.txt"]
    par_chemin = {f.chemin: f.contenu for f in fichiers}
    assert par_chemin["a.py"] == "print('salut')"
    assert par_chemin["src/c.txt"] == "imbriqué"


def test_produced_files_laisse_le_binaire_a_none():
    with isolated_workspace() as ws:
        (ws.path / "image.bin").write_bytes(b"\xff\xfe\x00\x01")
        (fichier,) = ws.produced_files()
    assert fichier.chemin == "image.bin"
    assert fichier.contenu is None


def test_produced_files_laisse_le_volumineux_a_none():
    with isolated_workspace() as ws:
        gros = ws.path / "gros.txt"
        gros.write_text("x" * (_MAX_CAPTURE_BYTES + 1), encoding="utf-8")
        (fichier,) = ws.produced_files()
    assert fichier.contenu is None


def test_produced_file_to_dict():
    pf = ProducedFile(chemin="a.py", contenu="print(1)")
    assert pf.to_dict() == {"chemin": "a.py", "contenu": "print(1)"}


def test_workspace_est_immuable():
    ws = Workspace(path=Path("."))
    with pytest.raises(FrozenInstanceError):
        ws.path = Path("/autre")  # type: ignore[misc]
