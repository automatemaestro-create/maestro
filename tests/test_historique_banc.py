"""L'historique du banc des scénarios : la série se lit, les passages d'avant y entrent (#1461).

Ce que `tests/test_scenarios.py` vérifie déjà : qu'un passage joué par
`maestro.scenarios.banc.main` — clone, worktree ou pilote — écrit sa ligne, et
qu'elle survit au retrait du worktree. Ici, ce qu'on fait de ces lignes :

① la **copie** lue par Git — sha, branche, iid, arbre modifié, tête détachée ;
② la **lecture** — taux de réussite, coût et durée médians, dernier vert, fenêtre ;
③ l'**import** des rapports d'avant, sans sha, et sans doublon au second import ;
④ la **ligne de commande** — texte, `--json`, refus d'usage.

Aucun réseau, aucun modèle : des lignes écrites à la main, des rapports en fichier,
et de vrais dépôts Git jetables.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from maestro.emplacements import racine_etat
from maestro.scenarios import historique
from maestro.scenarios.historique import (
    SOURCE_PASSAGE,
    SOURCE_RAPPORT,
    VARIABLE_HISTORIQUE,
    VERSION,
    Copie,
    Lecture,
    chemin_historique,
    consigner,
    copie_de_travail,
    en_texte,
    importer,
    lire,
    series,
)
from maestro.scenarios.modele import Rapport, Resultat

_IDENTITE = ("-c", "user.name=banc", "-c", "user.email=banc@example.invalid")


def _git(racine: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *_IDENTITE, "-C", str(racine), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _depot(racine: Path, branche: str = "main") -> str:
    racine.mkdir(parents=True)
    _git(racine, "init", "-q", "-b", branche)
    (racine / "a.txt").write_text("a\n", encoding="utf-8")
    _git(racine, "add", "-A")
    _git(racine, "commit", "-q", "-m", "base")
    return _git(racine, "rev-parse", "HEAD")


def _scenario(
    identifiant: str,
    verdict: str = "vert",
    *,
    cout: float | None = 1.0,
    duree: float = 60.0,
    rejoue: bool = False,
    empechement: bool = False,
) -> dict[str, Any]:
    return {
        "id": identifiant,
        "verdict": verdict,
        "cout_usd": cout,
        "duree_s": duree,
        "rejoue": rejoue,
        "empechement": empechement,
        "run_id": "",
    }


def _ligne(
    passage: str,
    scenarios: Sequence[dict[str, Any]],
    *,
    sha: str | None = "0123456789abcdef",
    branche: str | None = "main",
    modifiee: bool | None = False,
) -> dict[str, Any]:
    return {
        "version": VERSION,
        "source": SOURCE_PASSAGE,
        "passage": passage,
        "sha": sha,
        "branche": branche,
        "iid": None,
        "copie": "/copie",
        "modifiee": modifiee,
        "scenarios": list(scenarios),
    }


def _ecrire(fichier: Path, *lignes: dict[str, Any] | str) -> Path:
    fichier.parent.mkdir(parents=True, exist_ok=True)
    textes = [ligne if isinstance(ligne, str) else json.dumps(ligne) for ligne in lignes]
    fichier.write_text("".join(f"{texte}\n" for texte in textes), encoding="utf-8")
    return fichier


# --- ① La copie ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("branche", "iid"),
    [
        ("chore/1461-chaque-passage", 1461),
        ("chore/164", 164),
        ("main", None),
        ("feat/abc-12", None),
        ("1461-sans-type", None),
        ("Chore/1461-x", None),
        (None, None),
    ],
)
def test_l_iid_se_lit_sur_la_branche_d_un_ticket(branche: str | None, iid: int | None) -> None:
    """La règle de `gl_branch_iid` : `<type>/<iid>-<slug>`, le slug toléré absent."""
    assert Copie(racine="/x", branche=branche).iid == iid


def test_la_copie_dit_son_sha_sa_branche_et_un_arbre_modifie(tmp_path: Path) -> None:
    racine = tmp_path / "copie"
    sha = _depot(racine, "feat/1461-historique")

    propre = copie_de_travail(racine)
    (racine / "a.txt").write_text("modifié\n", encoding="utf-8")
    (racine / "nouveau.txt").write_text("non suivi\n", encoding="utf-8")
    modifiee = copie_de_travail(racine)

    assert (propre.sha, propre.branche, propre.iid, propre.modifiee) == (
        sha,
        "feat/1461-historique",
        1461,
        False,
    )
    assert propre.racine == str(racine.resolve())
    assert modifiee.modifiee is True, "un fichier suivi modifié : ce n'est plus ce sha"


def test_un_fichier_non_suivi_ne_fait_pas_un_arbre_modifie(tmp_path: Path) -> None:
    """Le clone principal porte des dossiers non suivis (bilans, sorties) : ils ne
    changent pas le code qui tourne, donc pas le sha qu'on attribue au passage."""
    racine = tmp_path / "copie"
    _depot(racine)
    (racine / "sortie-demo").mkdir()
    (racine / "sortie-demo" / "x.txt").write_text("x\n", encoding="utf-8")

    assert copie_de_travail(racine).modifiee is False


def test_une_tete_detachee_garde_son_sha_sans_branche_ni_iid(tmp_path: Path) -> None:
    """Le pilote peut jouer `main` intégrée sur une tête détachée : le sha suffit."""
    racine = tmp_path / "copie"
    sha = _depot(racine, "chore/1461-x")
    _git(racine, "checkout", "-q", "--detach")

    copie = copie_de_travail(racine)

    assert (copie.sha, copie.branche, copie.iid) == (sha, None, None)


def test_sans_git_la_copie_est_nommee_et_rien_d_autre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git absent du `PATH` : aucune exception, des champs vides."""
    monkeypatch.setenv("PATH", str(tmp_path / "vide"))

    copie = copie_de_travail(tmp_path)

    assert copie == Copie(racine=str(tmp_path.resolve()))


# --- ② L'écriture et la lecture ----------------------------------------------


def test_le_fichier_vit_sous_maestro_du_profil_ou_la_ou_sa_variable_le_met(tmp_path: Path) -> None:
    assert chemin_historique({}) == racine_etat() / "historique" / "scenarios.jsonl"
    ailleurs = tmp_path / "h.jsonl"
    assert chemin_historique({VARIABLE_HISTORIQUE: str(ailleurs)}) == ailleurs


def test_la_suite_n_ecrit_jamais_dans_l_historique_du_poste() -> None:
    """Le treizième garde-fou de `tests/conftest.py` : un fichier jetable par test."""
    assert racine_etat() not in chemin_historique().parents


def test_une_ligne_consignee_se_relit_avec_ce_que_le_passage_a_mesure(tmp_path: Path) -> None:
    fichier = tmp_path / "h.jsonl"
    rapport = Rapport(
        horodatage="20261009-101010",
        resultats=(
            Resultat(
                identifiant="S1",
                titre="Vider",
                verdict="vert",
                motif="vidé",
                duree_s=12.34567,
                run_id="r1",
                cout_usd=0.42,
            ),
            Resultat(
                identifiant="S9",
                titre="Chorale",
                verdict="rouge",
                motif="non",
                duree_s=900.0,
                cout_usd=None,
                rejoue=True,
                empechement=True,
            ),
        ),
    )
    copie = Copie(racine="/c", sha="abc", branche="chore/1461-x", modifiee=False)

    assert consigner(rapport, copie, chemin=fichier) == fichier
    consigner(rapport, copie, chemin=fichier)  # un second passage s'ajoute, n'écrase rien

    lecture = lire(fichier)
    assert lecture.illisibles == 0
    assert len(lecture.lignes) == 2
    ligne = lecture.lignes[0]
    assert (ligne["sha"], ligne["iid"], ligne["copie"]) == ("abc", 1461, "/c")
    assert ligne["scenarios"] == [
        {
            "id": "S1",
            "verdict": "vert",
            "cout_usd": 0.42,
            "duree_s": 12.346,
            "rejoue": False,
            "empechement": False,
            "run_id": "r1",
        },
        {
            "id": "S9",
            "verdict": "rouge",
            "cout_usd": None,
            "duree_s": 900.0,
            "rejoue": True,
            "empechement": True,
            "run_id": "",
        },
    ]


def test_une_ligne_tronquee_ou_d_une_autre_forme_est_ecartee_et_comptee(tmp_path: Path) -> None:
    """Un processus tué en pleine écriture laisse une ligne coupée : la série reste
    lisible, et l'écart se voit."""
    autre = _ligne("20261001-000000", [_scenario("S1")]) | {"version": VERSION + 1}
    fichier = _ecrire(
        tmp_path / "h.jsonl",
        _ligne("20261002-000000", [_scenario("S1")]),
        '{"version": 1, "passage": "2026',
        autre,
        _ligne("20261001-000000", [_scenario("S1")]),
    )

    lecture = lire(fichier)

    assert lecture.illisibles == 2
    assert [ligne["passage"] for ligne in lecture.lignes] == ["20261001-000000", "20261002-000000"]


def test_un_historique_absent_est_vide(tmp_path: Path) -> None:
    assert lire(tmp_path / "absent.jsonl") == Lecture(lignes=())


def test_la_serie_rend_taux_medianes_rejeux_empechements_et_dernier_vert() -> None:
    lignes = [
        _ligne("20261001-000000", [_scenario("S1", cout=1.0, duree=100)], sha="aaaaaaa1"),
        _ligne(
            "20261002-000000",
            [_scenario("S1", "rouge", cout=None, duree=10, empechement=True)],
            sha="bbbbbbb2",
        ),
        _ligne(
            "20261003-000000",
            [_scenario("S1", cout=3.0, duree=300, rejoue=True)],
            sha="ccccccc3",
            branche="chore/1461-x",
        ),
        _ligne("20261004-000000", [_scenario("S1", "rouge", cout=5.0, duree=500)], sha=None),
    ]

    (serie,) = series(lignes)

    assert (serie.passages, serie.verts, serie.taux) == (4, 2, 0.5)
    assert serie.cout_median_usd == 3.0, "un coût non rapporté n'entre pas dans la médiane"
    assert serie.duree_mediane_s == 200.0
    assert (serie.rejoues, serie.empechements) == (1, 1)
    assert serie.dernier_vert is not None
    assert serie.dernier_vert.to_dict() == {
        "passage": "20261003-000000",
        "sha": "ccccccc3",
        "branche": "chore/1461-x",
        "modifiee": False,
    }


def test_la_fenetre_se_compte_par_scenario_et_ne_borne_pas_le_dernier_vert() -> None:
    """S12, joué rarement, garde ses passages ; « depuis quand ? » regarde tout."""
    lignes = [
        _ligne("20261001-000000", [_scenario("S1"), _scenario("S12")], sha="vert0001"),
        _ligne("20261002-000000", [_scenario("S1", "rouge"), _scenario("S12", "rouge")]),
        _ligne("20261003-000000", [_scenario("S1", "rouge")]),
        _ligne("20261004-000000", [_scenario("S1", "rouge")]),
    ]

    s1, s12 = series(lignes, fenetre=2)

    assert (s1.identifiant, s1.passages, s1.verts) == ("S1", 2, 0)
    assert s1.dernier_vert is not None and s1.dernier_vert.sha == "vert0001"
    assert (s12.identifiant, s12.passages, s12.verts) == ("S12", 2, 1)


def test_les_scenarios_se_rangent_dans_l_ordre_naturel() -> None:
    lignes = [_ligne("20261001-000000", [_scenario(i) for i in ("S10", "S2", "S1", "S12")])]

    assert [s.identifiant for s in series(lignes)] == ["S1", "S2", "S10", "S12"]


def test_un_scenario_jamais_vert_et_sans_cout_le_dit() -> None:
    (serie,) = series([_ligne("20261001-000000", [_scenario("S7", "rouge", cout=None)])])

    assert serie.dernier_vert is None
    assert serie.cout_median_usd is None
    seule = _ligne("1", [_scenario("S7", "rouge", cout=None)])
    texte = en_texte(Lecture(lignes=(seule,)), Path("h"))
    assert "| S7 | 1 | 0% (0/1) | non rapporté |" in texte
    assert "| jamais |" in texte


def test_le_texte_dit_sha_inconnu_et_arbre_modifie_plutot_que_de_deviner() -> None:
    lecture = Lecture(
        lignes=(
            _ligne("20260922-100639", [_scenario("S1")], sha=None, branche=None),
            _ligne("20261009-101010", [_scenario("S2")], sha="0123456789ab", modifiee=True),
        ),
        illisibles=1,
    )

    texte = en_texte(lecture, Path("h.jsonl"))

    assert "20260922-100639 (sha inconnu)" in texte
    assert "20261009-101010 (0123456, arbre modifié sur main)" in texte
    assert "1 ligne(s) illisible(s) écartée(s)" in texte
    assert "2 passage(s)" in texte


# --- ③ L'import des passages d'avant ----------------------------------------


def _rapports(copie: Path, *passages: tuple[str, dict[str, Any]]) -> Path:
    dossier = copie / ".maestro" / "scenarios"
    for nom, charge in passages:
        (dossier / nom).mkdir(parents=True)
        (dossier / nom / "rapport.json").write_text(json.dumps(charge), encoding="utf-8")
    return dossier


def _rapport_ecrit(horodatage: str, *scenarios: Resultat) -> dict[str, Any]:
    return Rapport(horodatage=horodatage, resultats=scenarios).to_dict()


def _resultat(identifiant: str, verdict: str = "vert") -> Resultat:
    return Resultat(
        identifiant=identifiant, titre="t", verdict=verdict, motif="m", duree_s=30.0, cout_usd=0.3
    )


def test_les_rapports_d_avant_entrent_sans_sha_et_une_seule_fois(tmp_path: Path) -> None:
    """Le critère 2 : les rapports existants entrent, dans la forme qu'écrit le banc."""
    ecrit = _rapport_ecrit("20260927-070605", _resultat("S1"), _resultat("S2", "rouge"))
    copie = tmp_path / "clone"
    dossier = _rapports(
        copie,
        ("20260922-100639", _rapport_ecrit("20260922-100639", _resultat("S1", "rouge"))),
        ("20260927-070605", ecrit),
    )
    fichier = tmp_path / "h.jsonl"

    assert importer(dossier, chemin=fichier) == (2, 0)
    assert importer(dossier, chemin=fichier) == (0, 2), "rejouer l'import n'ajoute rien"

    lignes = lire(fichier).lignes
    assert [ligne["passage"] for ligne in lignes] == ["20260922-100639", "20260927-070605"]
    assert {(ligne["source"], ligne["sha"], ligne["branche"]) for ligne in lignes} == {
        (SOURCE_RAPPORT, None, None)
    }
    assert {ligne["copie"] for ligne in lignes} == {str(copie.resolve())}
    assert lignes[1]["scenarios"] == [
        {
            "id": s["id"],
            "verdict": s["verdict"],
            "cout_usd": s["cout_usd"],
            "duree_s": s["duree_s"],
            "rejoue": s["rejoue"],
            "empechement": s["empechement"],
            "run_id": s["run_id"],
        }
        for s in ecrit["scenarios"]
    ]


def test_un_rapport_illisible_arrete_l_import_et_se_nomme(tmp_path: Path) -> None:
    dossier = _rapports(tmp_path / "clone", ("20260922-100639", {"horodatage": "x"}))

    with pytest.raises(ValueError, match="20260922-100639"):
        importer(dossier, chemin=tmp_path / "h.jsonl")
    assert not (tmp_path / "h.jsonl").exists(), "rien d'écrit : un import partiel tairait un trou"


# --- ④ La ligne de commande -------------------------------------------------


class _Flux:
    def __init__(self) -> None:
        self.texte = ""

    def write(self, texte: str) -> int:
        self.texte += texte
        return len(texte)

    def flush(self) -> None:
        return None


def _main(fichier: Path, *argv: str) -> tuple[int, str, str]:
    sortie, erreur = _Flux(), _Flux()
    code = historique.main(list(argv), chemin=fichier, sortie=sortie, erreur=erreur)
    return code, sortie.texte, erreur.texte


def test_la_commande_rend_la_serie_en_json_pour_les_lots_suivants(tmp_path: Path) -> None:
    fichier = _ecrire(
        tmp_path / "h.jsonl",
        _ligne("20261001-000000", [_scenario("S1", cout=0.5, duree=120)], sha="abcdef1"),
        _ligne("20261002-000000", [_scenario("S1", "rouge", cout=1.5, duree=60)]),
    )

    code, sortie, _ = _main(fichier, "--json", "--fenetre", "5")

    assert code == historique.CODE_OK
    charge = json.loads(sortie)
    assert (charge["passages"], charge["fenetre"], charge["illisibles"]) == (2, 5, 0)
    (s1,) = charge["scenarios"]
    assert s1["taux"] == 0.5
    assert s1["cout_median_usd"] == 1.0
    assert s1["duree_mediane_s"] == 90.0
    assert s1["dernier_vert"]["sha"] == "abcdef1"


def test_la_commande_en_texte_nomme_son_fichier(tmp_path: Path) -> None:
    fichier = _ecrire(tmp_path / "h.jsonl", _ligne("20261001-000000", [_scenario("S4")]))

    code, sortie, _ = _main(fichier)

    assert code == historique.CODE_OK
    assert str(fichier) in sortie
    assert "| S4 | 1 | 100% (1/1) |" in sortie


def test_un_historique_vide_dit_comment_il_se_remplit(tmp_path: Path) -> None:
    code, sortie, _ = _main(tmp_path / "absent.jsonl")

    assert code == historique.CODE_OK
    assert "Aucun passage consigné" in sortie
    assert "--importer" in sortie


def test_la_commande_importe_les_rapports_d_un_dossier(tmp_path: Path) -> None:
    dossier = _rapports(
        tmp_path / "clone", ("20261001-000000", _rapport_ecrit("20261001-000000", _resultat("S1")))
    )
    fichier = tmp_path / "h.jsonl"

    code, sortie, _ = _main(fichier, "--importer", str(dossier))

    assert code == historique.CODE_OK
    assert "1 passage(s) importé(s)" in sortie and "sans sha" in sortie
    assert len(lire(fichier).lignes) == 1


@pytest.mark.parametrize(
    "argv",
    [["--fenetre", "0"], ["--fenetre", "x"], ["--inconnu"], ["--importer", "--json"]],
)
def test_un_usage_fautif_est_refuse(tmp_path: Path, argv: list[str]) -> None:
    code, _, erreur = _main(tmp_path / "h.jsonl", *argv)

    assert code == historique.CODE_USAGE
    assert "Usage" in erreur
