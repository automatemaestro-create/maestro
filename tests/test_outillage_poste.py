"""L'outillage d'un projet neuf regarde le poste, et se revérifie dès que le projet le permet (#1343).

Relevé par S9 sur la vraie stack : l'outillage d'un projet neuf recommandait `uv`,
`ruff`, `typst`, `pandoc` — absents du poste —, écrivait leurs commandes « à vérifier »
parce que le dossier était vide, et personne ne les revérifiait une fois le projet
construit. `AGENTS.md` prescrivait donc aux agents des commandes qui échouent, et
l'équipe construisait autrement.

Quatre étages, du moteur au banc :

① **ce que le poste répond se joue même sur un dossier vide** — une commande qui ne peut
   pas encore se jouer n'en appelle pas moins des programmes, et leur présence se
   vérifie : un outil absent rend la commande **échouée**, avec la sortie de la sonde,
   jamais écrite comme la marche à suivre (`maestro.outillage.verification`) ;
② **la recommandation regarde le poste** — chaque option dit les outils qu'elle
   demande, Maestro les sonde, une option qui repose sur un outil absent ne reste pas
   recommandée, et ce que le poste a répondu est dit au modèle
   (`maestro.outillage.questionnaire`, `maestro.controltower.outillage`) ;
③ **ce qui ne pouvait pas encore se jouer se joue dès que le projet le permet** — à la
   fin d'un run, les commandes que l'outillage n'avait pas pu vérifier sont rejouées
   sur le projet construit, et la pièce qui change est proposée dans le fil, portée par
   le récit de fin (`maestro.controltower.pieces`, `maestro.controltower.recit`) ;
④ le banc (S9, S10) tranche cette pièce avant de rejouer lui-même les commandes — dans
   `tests/test_scenarios.py`, avec les autres oracles des scénarios.

Les commandes sont jouées par un joueur doublé, sauf sous `@pytest.mark.commandes_jouees`
où un vrai bash répond.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from maestro.outillage import (
    CHEMIN_MANIFESTE,
    Commande,
    Constats,
    generer_outillage,
    recommander,
)
from maestro.outillage.verification import (
    A_VERIFIER,
    ECHOUEE,
    VERIFIEE,
    Verificateur,
    Verification,
    programmes,
    sonde,
)
from maestro.projets.modele import Perimetre, Projet
from maestro.sandbox import verification as execution

#: L'interpréteur réel, relevé **avant** que la garde du conftest ne le retire.
BASH = execution.interprete()
besoin_de_bash = pytest.mark.skipif(BASH is None, reason="aucun bash sur ce poste")

#: Un interpréteur factice : le joueur doublé ne le lance jamais.
FAUX_BASH = ("bash", "-c")

SOURCE_CHOIX = {
    "type": "choix",
    "projet_id": "prj-chorale",
    "reference": "nature=carnet de chants",
    "resume": "carnet de chants",
}
QUAND = "2026-09-27T12:00:00+00:00"

#: Ce que bash répond d'un programme qu'il ne trouve pas — la sortie gardée, jamais lue.
INTROUVABLE = execution.Execution(code=1, sortie="bash: type: uv: not found", duree_s=0.1)


@pytest.fixture(autouse=True)
def _maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`Path.home()` sous `tmp_path` : `valider_racine` refuse `AppData` sous Windows (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


class _Joueur:
    """Un joueur doublé : un résultat par commande (0 par défaut), et ce qu'il a joué."""

    def __init__(self, resultats: Mapping[str, execution.Execution] | None = None) -> None:
        self._resultats = dict(resultats or {})
        self.joues: list[str] = []

    def __call__(
        self, commande: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        self.joues.append(commande)
        return self._resultats.get(commande, execution.Execution(code=0, sortie="ok", duree_s=0.1))


def _neuf(*commandes: tuple[str, str]) -> Constats:
    """Les constats d'un projet neuf : des commandes de convention, qui vivront dans un manifeste."""
    return Constats(
        commandes=tuple(
            Commande(usage=usage, commande=texte, chemin="pyproject.toml", origine="convention")
            for usage, texte in commandes
        )
    )


def _generer(racine: Path, constats: Constats, verificateur: Verificateur) -> Any:
    return generer_outillage(
        Projet(id="prj-chorale", nom="Chorale", racine=racine.as_posix()),
        constats,
        recommander(constats),
        source=SOURCE_CHOIX,
        horodatage=QUAND,
        verificateur=verificateur,
    )


def _ligne(texte: str, commande: str) -> str:
    return next(ligne for ligne in texte.splitlines() if f"`{commande}`" in ligne)


# --------------------------------------------------------------------------- #
# ① Ce que le poste répond se joue même sur un dossier vide                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("commande", "attendus"),
    [
        ("uv run pytest", ("uv",)),
        ("typst compile carnet.typ", ("typst",)),
        ("env CI=1 npx vitest run", ("npx",)),
        ("PYTHONPATH=. python -m pytest", ("python",)),
        ("npm ci && npm test", ("npm",)),
        ("bash scripts/tests.sh", ("bash",)),
        # Un fichier du projet, pas un outil du poste : il existera, ou pas, avec le projet.
        ("./gradlew test", ()),
        # Ce qu'une substitution exécute n'est pas dans le texte : rien ne se sonde.
        ("python $(which app)", ()),
    ],
)
def test_les_programmes_d_une_commande_sont_ceux_qu_elle_appelle(
    commande: str, attendus: tuple[str, ...]
) -> None:
    assert programmes(commande) == attendus


def test_un_projet_neuf_dont_l_outil_manque_au_poste_ecrit_sa_commande_echouee(
    tmp_path: Path,
) -> None:
    """Le défaut de #1343 : `uv` absent, ses commandes étaient écrites « à vérifier »."""
    racine = tmp_path / "maison" / "chorale"
    racine.mkdir(parents=True)
    joueur = _Joueur({sonde("uv"): INTROUVABLE})
    constats = _neuf(
        ("installer", "uv sync"),
        ("construire", "python assembler.py"),
        ("tester", "uv run pytest"),
    )

    preparation = _generer(racine, constats, Verificateur(joueur=joueur, interprete=FAUX_BASH))

    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    for commande in ("uv sync", "uv run pytest"):
        assert verdicts[commande].etat == ECHOUEE
        assert verdicts[commande].code == 1
        assert verdicts[commande].sortie == "bash: type: uv: not found"
        assert verdicts[commande].raison.startswith("`uv` est introuvable sur ce poste")
    # L'outil est là : c'est le projet qui manque encore, et la commande attend.
    assert verdicts["python assembler.py"].etat == A_VERIFIER
    assert "aucun fichier en dehors de son outillage" in verdicts["python assembler.py"].raison
    # Rien du projet n'est joué — seulement ce que le poste dit de ses outils, une fois chacun.
    assert sorted(joueur.joues) == sorted({sonde("uv"), sonde("python")})
    agents = (racine / "AGENTS.md").read_text(encoding="utf-8")
    assert "⚠ **Échouée**" in _ligne(agents, "uv sync")
    assert "`uv` est introuvable sur ce poste" in _ligne(agents, "uv sync")
    assert "**À vérifier**" in _ligne(agents, "python assembler.py")


def test_une_commande_dont_le_fichier_manque_encore_dit_aussi_l_outil_absent(
    tmp_path: Path,
) -> None:
    """Le projet a ses fichiers, pas encore celui où la commande vivra : l'outil se sonde."""
    racine = tmp_path / "maison" / "chorale"
    (racine / "chants").mkdir(parents=True)
    (racine / "chants" / "fontaine.txt").write_text("À la claire fontaine\n", encoding="utf-8")
    joueur = _Joueur({sonde("typst"): INTROUVABLE})

    preparation = _generer(
        racine,
        _neuf(("construire", "typst compile carnet.typ"), ("tester", "python -m unittest")),
        Verificateur(joueur=joueur, interprete=FAUX_BASH),
    )

    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    assert verdicts["typst compile carnet.typ"].etat == ECHOUEE
    assert verdicts["python -m unittest"].etat == A_VERIFIER
    assert verdicts["python -m unittest"].raison.startswith("`pyproject.toml` n'existe pas encore")


def test_un_acte_hors_du_projet_reste_a_une_personne_meme_sur_un_dossier_vide(
    tmp_path: Path,
) -> None:
    """La portée d'abord : ce qu'un agent n'aurait pas fait seul n'est ni joué ni sondé."""
    racine = tmp_path / "maison" / "chorale"
    racine.mkdir(parents=True)
    joueur = _Joueur()

    preparation = _generer(
        racine,
        _neuf(("installer", "winget install --id JohnMacFarlane.Pandoc")),
        Verificateur(joueur=joueur, interprete=FAUX_BASH),
    )

    (verdict,) = preparation.rapport.verifications
    assert verdict.etat == A_VERIFIER
    assert verdict.raison.startswith("pas jouée — commande hors de la portée")
    assert joueur.joues == []


@pytest.mark.commandes_jouees
@besoin_de_bash
def test_un_vrai_bash_dit_quel_outil_manque_au_poste(tmp_path: Path) -> None:
    """La mécanique réelle : `type` dans le bash des agents, dans une copie du projet."""
    racine = tmp_path / "maison" / "chorale"
    racine.mkdir(parents=True)
    absent = "maestro-outil-absent-1343"

    preparation = _generer(
        racine,
        _neuf(("construire", f"{absent} carnet.txt"), ("tester", "bash scripts/tests.sh")),
        Verificateur(),
    )

    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    assert verdicts[f"{absent} carnet.txt"].etat == ECHOUEE, verdicts
    assert verdicts[f"{absent} carnet.txt"].code not in (None, 0)
    assert absent in verdicts[f"{absent} carnet.txt"].sortie
    assert verdicts["bash scripts/tests.sh"].etat == A_VERIFIER
    manifeste = json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))
    assert {v["etat"] for v in manifeste["verifications"]} == {ECHOUEE, A_VERIFIER}


@pytest.mark.commandes_jouees
@besoin_de_bash
def test_sonder_le_poste_rend_ce_que_bash_trouve_et_ce_qu_il_ne_trouve_pas() -> None:
    """Le verbe que le questionnaire consulte : présent, absent — et rien sur ce qu'il ignore."""
    absent = "maestro-outil-absent-1343"

    poste = Verificateur().sonder(("bash", absent, "bash"))

    assert poste == {"bash": True, absent: False}


def test_sans_bash_le_poste_ne_repond_rien() -> None:
    """Sous la garde du conftest : pas d'interpréteur, donc aucun fait du poste — jamais « absent »."""
    assert Verificateur().sonder(("uv",)) == {}


def test_une_commande_deja_verifiee_ne_se_sonde_pas(tmp_path: Path) -> None:
    """Un verdict joué est repris tel quel (#1161) : il n'y a rien à redemander au poste."""
    racine = tmp_path / "maison" / "chorale"
    racine.mkdir(parents=True)
    joueur = _Joueur()
    constats = _neuf(("tester", "python -m unittest"))
    connue = Verification(
        usage="tester",
        commande="python -m unittest",
        etat=VERIFIEE,
        raison="elle a rendu la main sans erreur",
    )

    verdicts = Verificateur(joueur=joueur, interprete=FAUX_BASH).verifier(
        racine,
        constats,
        recommander(constats),
        perimetre=Perimetre(),
        connues={connue.commande: connue},
    )

    assert verdicts == (connue,)
    assert joueur.joues == []
