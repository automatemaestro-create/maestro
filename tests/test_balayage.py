"""Le parcours partagé des balayages du dépôt (#1546) — voir [`balayage.py`](balayage.py).

Deux promesses, chacune prouvée sur un échantillon FAUTIF avant d'être crue : l'arbre de test porte
les pièges qu'un `rglob` ramasse, et le parcours les laisse dehors. La seconde est plus forte que
la première : un filtre posé APRÈS le parcours rendrait la même liste, mais paierait la descente
entière — et c'est cette descente, à travers le montage du conteneur, qui a coûté 20 min au filet.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from balayage import fichiers_du_depot

#: Ce que le dépôt porte : un balayage doit tout lire. Le `worktrees` des compétences n'est pas
#: celui de `.claude/` — seul le second abrite des copies du dépôt.
VOULUS = (
    ".claude/commands/ticket-start.md",
    ".claude/skills/verify/worktrees/note.md",
    "docs/10-workflow-git.md",
    "scripts/gitlab/lib.sh",
)
#: Ce qui n'est pas le dépôt : une autre copie, du bytecode, des dépendances.
PIEGES = (
    ".claude/worktrees/1234-autre-ticket/.claude/commands/ticket-start.md",
    ".claude/worktrees/1234-autre-ticket/apps/web/node_modules/paquet/README.md",
    "scripts/__pycache__/outil.cpython-311.pyc.2594",
    "apps/desktop/node_modules/electron/index.js",
)


@pytest.fixture
def depot(tmp_path: Path) -> Path:
    for relatif in (*VOULUS, *PIEGES):
        chemin = tmp_path / relatif
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text("contenu\n", encoding="utf-8")
    return tmp_path


def _relatifs(racine: Path, chemins) -> list[str]:
    return [chemin.relative_to(racine).as_posix() for chemin in chemins]


def test_l_echantillon_porte_les_pieges_qu_un_rglob_ramasse(depot: Path) -> None:
    vus = _relatifs(depot, (p for p in depot.rglob("*") if p.is_file()))
    assert set(PIEGES) <= set(vus), "l'échantillon n'est pas fautif : il ne prouverait rien"


def test_le_parcours_rend_le_depot_et_rien_d_autre(depot: Path) -> None:
    dossiers = (depot / ".claude", depot / "docs", depot / "scripts", depot / "apps")
    assert _relatifs(depot, fichiers_du_depot(*dossiers)) == sorted(VOULUS)


def test_les_worktrees_sont_ecartes_aussi_depuis_la_racine(depot: Path) -> None:
    """`.claude/` balayé comme sous-dossier, et non comme point de départ."""
    vus = _relatifs(depot, fichiers_du_depot(depot))
    assert vus == sorted(VOULUS)


def test_le_parcours_ne_descend_pas_dans_ce_qu_il_ecarte(
    depot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Écarter n'est pas filtrer : aucun répertoire écarté n'est même ouvert."""
    ouverts: list[str] = []
    scandir = os.scandir

    def espion(chemin):
        ouverts.append(Path(chemin).relative_to(depot).as_posix())
        return scandir(chemin)

    monkeypatch.setattr(os, "scandir", espion)
    list(fichiers_du_depot(depot))
    assert ouverts, "l'espion n'a rien vu : il ne prouverait rien"
    ecartes = [o for o in ouverts if "node_modules" in o or "__pycache__" in o
               or o.startswith(".claude/worktrees")]
    assert not ecartes, f"descente dans ce qui est écarté : {ecartes}"
