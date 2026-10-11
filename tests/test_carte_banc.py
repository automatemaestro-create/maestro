"""La carte du banc : quel code chaque scénario a exécuté (#1463).

Deux pièces, éprouvées chacune là où elle vit :

① **La sonde** (`maestro.scenarios.sonde.sitecustomize`) se juge sur de **vrais
   processus** : c'est Python qui la charge au démarrage, par `PYTHONPATH`, et ce
   qu'elle promet — tous les processus, rangés sous la borne du moment, réarmés à
   chaque borne — ne se voit pas dans le processus de la suite (qui porte déjà la
   couverture de `pytest-cov`). Ce qui doit arriver dans l'ordre l'est par une
   **barrière** — le processus attend de voir la borne, il ne dort pas au jugé (#292).
② **L'assemblage** (`maestro.scenarios.carte`) se juge sur des relevés écrits à la
   main : filtre des fichiers suivis, configuration du banc rendue à sa source,
   démarrage à part, scénario vide qui figure.

Le passage du banc entier, doublé, est dans `tests/test_scenarios.py` (§③ter).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from maestro.scenarios import carte as carte_module
from maestro.scenarios.carte import (
    Carte,
    CarteDuPassage,
    Empreinte,
    Processus,
    assembler,
    charger,
    preparer,
)
from maestro.scenarios.modele import Rapport, Resultat
from maestro.scenarios.sonde import sitecustomize as sonde
from maestro.scenarios.sonde.sitecustomize import Sonde, _en_lecture

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_SONDE = RACINE / "maestro" / "scenarios" / "sonde"

#: Ce qu'un processus sondé peut prendre — import de `maestro` compris, sous charge.
DELAI_S = 120


# ── ① La sonde, sur de vrais processus ─────────────────────────────────────────


def _environnement(dossier: Path | None, *pythonpath: Path) -> dict[str, str]:
    """L'environnement d'un processus de la stack : la sonde en tête de `PYTHONPATH`."""
    env = {
        cle: valeur
        for cle, valeur in os.environ.items()
        if cle not in {sonde.VARIABLE_DOSSIER, "PYTHONPATH"}
    }
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in (DOSSIER_SONDE, *pythonpath))
    env["PYTHONIOENCODING"] = "utf-8"
    if dossier is not None:
        env[sonde.VARIABLE_DOSSIER] = str(dossier)
    return env


def _jouer(code: str, dossier: Path | None, *args: str, pythonpath: tuple[Path, ...] = ()) -> str:
    """Joue `code` dans un processus sondé, depuis la copie (son `maestro` est le sien)."""
    fini = subprocess.run(  # noqa: S603 - l'interpréteur de la suite, code écrit ici
        [sys.executable, "-c", code, *args],
        cwd=RACINE,
        env=_environnement(dossier, *pythonpath),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=DELAI_S,
        check=False,
    )
    assert fini.returncode == 0, fini.stderr
    return fini.stdout


def _releves(dossier: Path) -> list[list[str]]:
    """Les lignes des relevés de tous les processus, champ par champ."""
    return [
        ligne.split("\t")
        for fichier in sorted((dossier / sonde.DOSSIER_TRACES).glob("*.tsv"))
        for ligne in fichier.read_text(encoding="utf-8").splitlines()
    ]


def _fonctions(dossier: Path, segment: str) -> set[tuple[str, str]]:
    return {
        (champs[2], champs[3])
        for champs in _releves(dossier)
        if champs[0] == segment and champs[1] == sonde.SORTE_FONCTION
    }


def _pids(dossier: Path, segment: str) -> set[str]:
    return {
        champs[2]
        for champs in _releves(dossier)
        if champs[0] == segment and champs[1] == sonde.SORTE_PROCESSUS
    }


def _dossier(tmp_path: Path, segment: str | None = None) -> Path:
    dossier = tmp_path / "carte"
    dossier.mkdir()
    if segment is not None:
        (dossier / sonde.FICHIER_SEGMENT).write_text(segment, encoding="utf-8")
    return dossier


_DEUX_PROCESSUS = """
import pathlib, subprocess, sys
from maestro import fichiers
pathlib.Path("pyproject.toml").read_bytes()
fichiers.retirer_arbre(pathlib.Path(sys.argv[1]) / "absent")
subprocess.run(
    [sys.executable, "-c", "from maestro import espace; espace.racine_de_la_copie()"],
    check=True,
)
"""


def test_chaque_processus_range_ses_fonctions_et_ses_lectures_sous_le_scenario(
    tmp_path: Path,
) -> None:
    """Le critère 1, au niveau d'un processus : un processus et celui qu'il lance —
    comme l'API et son hôte détaché — relèvent tous deux, sous le scénario borné, les
    fonctions de la copie qu'ils exécutent (par nom qualifié) et les fichiers lus."""
    dossier = _dossier(tmp_path, "S1")

    _jouer(_DEUX_PROCESSUS, dossier, str(tmp_path))

    fonctions = _fonctions(dossier, "S1")
    assert ("maestro/fichiers.py", "retirer_arbre") in fonctions, "le processus lancé"
    assert ("maestro/espace.py", "racine_de_la_copie") in fonctions, "celui qu'il a lancé"
    lus = {c[2] for c in _releves(dossier) if c[0] == "S1" and c[1] == sonde.SORTE_FICHIER}
    assert "pyproject.toml" in lus
    assert len(_pids(dossier, "S1")) == 2, "deux processus, deux relevés"
    assert {c[0] for c in _releves(dossier)} == {"S1"}, "la borne lue au démarrage vaut d'emblée"
    assert not any("sonde/sitecustomize.py" in "\t".join(c) for c in _releves(dossier)), (
        "la sonde ne se relève pas elle-même"
    )


_UN_PROCESSUS_QUI_DURE = """
import sys, time
import sitecustomize
from maestro import espace
sonde = sitecustomize.SONDE
assert sonde is not None, "sonde absente"
espace.racine_de_la_copie()
print("pret", flush=True)
for attendu in iter(sys.stdin.readline, ""):
    attendu = attendu.strip()
    while sonde.seau.segment != attendu:  # la barrière : la borne vue, pas un délai
        time.sleep(0.01)
    espace.racine_de_la_copie()
    espace.nom_de_worktree(espace.racine_de_la_copie())
    print("vu " + attendu, flush=True)
"""


def test_un_processus_qui_dure_range_sous_chaque_borne_ce_qu_il_execute_apres_elle(
    tmp_path: Path,
) -> None:
    """L'API vit tout le passage : une fonction déjà exécutée sous S1 se relève **à
    nouveau** sous S2 (réarmée à la borne), et ce qui n'a tourné qu'après la borne
    n'est que sous S2. Après `_fin`, plus rien n'est relevé."""
    dossier = _dossier(tmp_path, "S1")
    processus = subprocess.Popen(  # noqa: S603 - l'interpréteur de la suite, code écrit ici
        [sys.executable, "-c", _UN_PROCESSUS_QUI_DURE],
        cwd=RACINE,
        env=_environnement(dossier),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert processus.stdin is not None and processus.stdout is not None
    try:
        assert processus.stdout.readline().strip() == "pret"
        for segment in ("S2", sonde.SEGMENT_FIN):
            (dossier / sonde.FICHIER_SEGMENT).write_text(segment, encoding="utf-8")
            processus.stdin.write(segment + "\n")
            processus.stdin.flush()
            assert processus.stdout.readline().strip() == f"vu {segment}"
        processus.stdin.close()
        assert processus.wait(timeout=DELAI_S) == 0, processus.stderr.read()
    finally:
        processus.kill()

    racine = ("maestro/espace.py", "racine_de_la_copie")
    nom = ("maestro/espace.py", "nom_de_worktree")
    assert racine in _fonctions(dossier, "S1") and nom not in _fonctions(dossier, "S1")
    assert {racine, nom} <= _fonctions(dossier, "S2"), "réarmée à la borne"
    assert sonde.SEGMENT_FIN not in {c[0] for c in _releves(dossier)}, "éteinte à la fin"


def test_un_processus_ne_apres_la_fin_ne_porte_pas_la_sonde(tmp_path: Path) -> None:
    """La stack que le passage laisse servie tourne sans sonde : un run lancé ensuite
    par la personne n'est ni ralenti, ni relevé."""
    dossier = _dossier(tmp_path, sonde.SEGMENT_FIN)

    sortie = _jouer("import sitecustomize; print(sitecustomize.SONDE)", dossier)

    assert sortie.strip() == "None"
    assert _releves(dossier) == []


def test_sans_dossier_la_sonde_ne_fait_rien(tmp_path: Path) -> None:
    """Le `PYTHONPATH` seul ne mesure rien : le dossier nomme le passage."""
    assert _jouer("import sitecustomize; print(sitecustomize.SONDE)", None).strip() == "None"


def test_le_sitecustomize_que_la_sonde_masque_est_charge_apres_elle(tmp_path: Path) -> None:
    """Un projet que le banc fait tourner hérite du `PYTHONPATH` : son propre
    `sitecustomize`, s'il en a un, ne doit pas disparaître derrière la sonde."""
    autre = tmp_path / "autre"
    autre.mkdir()
    temoin = tmp_path / "temoin.txt"
    (autre / "sitecustomize.py").write_text(
        f"open({str(temoin)!r}, 'w').write('charge')\n", encoding="utf-8"
    )

    _jouer("pass", _dossier(tmp_path, "S1"), pythonpath=(autre,))

    assert temoin.read_text(encoding="utf-8") == "charge"


_REPLI = """
import sys
from maestro.scenarios.sonde.sitecustomize import Sonde
sonde = Sonde(sys.argv[1], monitoring=False)
sonde.demarrer()
from maestro import espace
espace.racine_de_la_copie()
sonde.arreter()
print(sonde.monitoring)
"""


def test_le_repli_par_settrace_releve_les_memes_fonctions(tmp_path: Path) -> None:
    """Sous Python 3.11, pas de `sys.monitoring` : le repli relève la même chose."""
    dossier = _dossier(tmp_path)

    sortie = _jouer(_REPLI, None, str(dossier))

    assert sortie.strip() == "False"
    assert ("maestro/espace.py", "racine_de_la_copie") in _fonctions(
        dossier, sonde.SEGMENT_DEMARRAGE
    ), "avant toute borne : le démarrage"


@pytest.mark.parametrize(
    ("mode", "drapeaux", "lecture"),
    [
        ("r", 0, True),
        ("rb", 0, True),
        ("w", 0, False),
        ("a", 0, False),
        ("r+", 0, False),
        ("x", 0, False),
        (None, os.O_RDONLY, True),
        (None, os.O_WRONLY, False),
        (None, os.O_RDWR, False),
        (None, os.O_RDONLY | os.O_CREAT, False),
        (None, None, False),
    ],
)
def test_seules_les_lectures_comptent(
    mode: str | None, drapeaux: int | None, lecture: bool
) -> None:
    """Un fichier qu'un scénario écrit n'est pas une donnée qu'il reçoit."""
    assert _en_lecture(mode, drapeaux) is lecture


def test_seul_ce_qui_est_sous_la_copie_se_releve(tmp_path: Path) -> None:
    """La copie, en `/` ; ni l'interpréteur (`.venv`), ni l'outillage, ni le dossier de
    la carte, ni ce qui est hors de la copie."""
    copie = tmp_path / "copie"
    dossier = copie / ".maestro" / "carte"
    une = Sonde(str(dossier), racine=str(copie))

    assert une.relatif(str(copie / "maestro" / "x.py")) == "maestro/x.py"
    assert une.relatif(str(copie / "core" / "agents" / "dev.md")) == "core/agents/dev.md"
    assert une.relatif(str(copie / ".maestro" / "banc" / "agents" / "dev.md")) == (
        ".maestro/banc/agents/dev.md"
    ), "la configuration du banc : rendue à sa source à l'assemblage"
    assert une.relatif(str(copie / ".venv" / "Lib" / "site-packages" / "x.py")) is None
    assert une.relatif(str(copie / "apps" / "web" / "node_modules" / "x.js")) is None
    assert une.relatif(str(dossier / "segment")) is None
    assert une.relatif(str(tmp_path / "ailleurs" / "x.py")) is None


# ── ② L'assemblage ────────────────────────────────────────────────────────────


def _ecrire_releve(dossier: Path, nom: str, *lignes: str) -> None:
    traces = dossier / sonde.DOSSIER_TRACES
    traces.mkdir(parents=True, exist_ok=True)
    (traces / f"{nom}.tsv").write_text("".join(f"{ligne}\n" for ligne in lignes), "utf-8")


_SUIVIS = {
    chemin.casefold() if os.name == "nt" else chemin: chemin
    for chemin in (
        "maestro/api.py",
        "maestro/run.py",
        "core/agents/dev.md",
        "core/playbooks/qa.md",
        "pyproject.toml",
    )
}
_MIROIRS = {".maestro/banc/agents": "core/agents"}


def test_l_assemblage_rend_un_ensemble_par_scenario_et_le_demarrage_a_part(
    tmp_path: Path,
) -> None:
    """Par scénario joué : fonctions par fichier, données lues, processus — et le
    démarrage, commun à tous, à part. Un scénario sans relevé figure, vide."""
    _ecrire_releve(
        tmp_path,
        "100-api",
        "_demarrage\tp\t100\t-m maestro.controltower.cli --etat-banc",
        "_demarrage\tf\tmaestro/api.py\tcreer_app",
        "_demarrage\td\tpyproject.toml",
        "S1\tp\t100\t-m maestro.controltower.cli --etat-banc",
        "S1\tf\tmaestro/api.py\tRoutes.lancer",
        "S1\tf\tmaestro/api.py\tRoutes.lancer",
        "S1\td\t.maestro/banc/agents/dev.md",
    )
    _ecrire_releve(
        tmp_path,
        "200-run",
        "S1\tp\t200\t-m maestro.controltower.hote_detache",
        "S1\tf\tmaestro/run.py\t<module>",
        "S1\tf\tmaestro/run.py\tExecuteur.executer.<locals>.etape",
        "S1\td\tcore/playbooks/qa.md",
        "S1\tf\tmaestro/run.p",  # tronquée par un processus tué
    )

    carte = assembler(
        tmp_path / sonde.DOSSIER_TRACES,
        passage="20261011-101010",
        copie=tmp_path,
        scenarios=["S1", "S2"],
        suivis=_SUIVIS,
        miroirs=_MIROIRS,
    )

    assert carte.demarrage.fonctions == {"maestro/api.py": ("creer_app",)}
    assert carte.demarrage.fichiers == ("pyproject.toml",)
    s1 = carte.scenarios["S1"]
    assert s1.fonctions == {
        "maestro/api.py": ("Routes.lancer",),
        "maestro/run.py": ("<module>", "Executeur.executer.<locals>.etape"),
    }
    assert s1.fichiers == ("core/agents/dev.md", "core/playbooks/qa.md"), (
        "la configuration lue dans le banc est rendue au fichier suivi dont elle est la copie"
    )
    assert [p.pid for p in s1.processus] == [100, 200]
    assert carte.scenarios["S2"] == Empreinte(), "joué sans relevé : il figure, vide"
    assert carte.stack_mesuree


def test_seuls_les_fichiers_suivis_entrent(tmp_path: Path) -> None:
    """L'état d'exécution, `.env`, un module non suivi, un chemin sous un crochet
    d'import : rien de cela n'est du dépôt, donc rien n'entre."""
    _ecrire_releve(
        tmp_path,
        "100",
        "S1\tp\t100\t-m maestro.controltower.cli",
        "S1\td\t.env",
        "S1\td\t.maestro/banc/chat/fil.json",
        "S1\td\t__editable__.maestro-0.0.0.finder.__path_hook__",
        "S1\tf\tmaestro/brouillon.py\tessai",
        "S1\td\tcore/agents/dev.md",
    )

    carte = assembler(
        tmp_path / sonde.DOSSIER_TRACES,
        passage="p",
        copie=tmp_path,
        scenarios=["S1"],
        suivis=_SUIVIS,
        miroirs=_MIROIRS,
    )

    assert carte.scenarios["S1"].fonctions == {}
    assert carte.scenarios["S1"].fichiers == ("core/agents/dev.md",)


def test_sans_git_les_dossiers_caches_sont_ecartes_et_c_est_dit(tmp_path: Path) -> None:
    _ecrire_releve(
        tmp_path,
        "100",
        "S1\tp\t100\t-m maestro.controltower.cli",
        "S1\td\t.env",
        "S1\tf\tmaestro/api.py\tf",
    )

    carte = assembler(
        tmp_path / sonde.DOSSIER_TRACES,
        passage="p",
        copie=tmp_path,
        scenarios=["S1"],
        suivis=None,
        miroirs={},
    )

    assert carte.scenarios["S1"].fichiers == ()
    assert carte.scenarios["S1"].fonctions == {"maestro/api.py": ("f",)}
    assert carte.to_dict()["filtre"] != "git"
    assert any("Git illisible" in ligne for ligne in carte_module.en_lignes(carte))


def test_une_carte_ou_seul_le_banc_a_ete_mesure_dit_la_stack_non_mesuree(
    tmp_path: Path,
) -> None:
    """Une stack démarrée sans la sonde ne rend pas des ensembles vides pour une mesure."""
    _ecrire_releve(
        tmp_path,
        "100",
        "S1\tp\t100\t-m maestro.scenarios --sauver-etat --temoin x",
        "S1\tf\tmaestro/api.py\tf",
    )

    carte = assembler(
        tmp_path / sonde.DOSSIER_TRACES,
        passage="p",
        copie=tmp_path,
        scenarios=["S1"],
        suivis=_SUIVIS,
        miroirs={},
    )

    assert not carte.stack_mesuree
    assert any("stack non mesurée" in ligne for ligne in carte_module.en_lignes(carte))


def test_une_carte_ecrite_se_relit_telle_quelle(tmp_path: Path) -> None:
    carte = CarteDuPassage(
        passage="20261011-101010",
        copie=str(tmp_path),
        demarrage=Empreinte(fonctions={"maestro/api.py": ("creer_app",)}),
        scenarios={
            "S1": Empreinte(
                fonctions={"maestro/run.py": ("a", "B.c")},
                fichiers=("core/agents/dev.md",),
                processus=(Processus(pid=1, commande="-m maestro.controltower.cli"),),
            )
        },
    )
    chemin = tmp_path / carte_module.FICHIER_CARTE
    chemin.write_text(json.dumps(carte.to_dict()), encoding="utf-8")

    assert charger(chemin) == carte
    assert carte.to_dict()["granularite"] == "fonction"


def test_une_carte_d_une_autre_forme_est_refusee(tmp_path: Path) -> None:
    chemin = tmp_path / carte_module.FICHIER_CARTE
    chemin.write_text(json.dumps({"version": 99}), encoding="utf-8")

    with pytest.raises(ValueError, match="autre forme"):
        charger(chemin)


def test_borner_ecrit_la_borne_puis_laisse_a_chacun_le_temps_de_la_voir(tmp_path: Path) -> None:
    attentes: list[float] = []
    carte = Carte(tmp_path / "carte", dormir=attentes.append)

    carte.borner("S3")
    assert (tmp_path / "carte" / sonde.FICHIER_SEGMENT).read_text(encoding="utf-8") == "S3"
    carte.finir()

    assert (tmp_path / "carte" / sonde.FICHIER_SEGMENT).read_text(encoding="utf-8") == (
        sonde.SEGMENT_FIN
    )
    assert attentes == [carte_module.ATTENTE_BORNE_S] * 2
    assert carte_module.ATTENTE_BORNE_S > 2 * sonde.PERIODE_S, "plusieurs périodes, pas une"


def test_la_carte_se_lit_dans_l_environnement_que_start_sh_pose(tmp_path: Path) -> None:
    assert Carte.depuis_environnement({}) is None
    posee = Carte.depuis_environnement({sonde.VARIABLE_DOSSIER: str(tmp_path)})
    assert posee is not None and posee.dossier == tmp_path


def test_l_ecriture_range_la_carte_a_cote_du_rapport(tmp_path: Path) -> None:
    dossier = tmp_path / "carte"
    _ecrire_releve(
        dossier,
        "100",
        "S1\tp\t100\t-m maestro.controltower.cli",
        "S1\tf\tmaestro/api.py\tf",
    )
    rapport = Rapport(
        horodatage="20261011-101010",
        resultats=(Resultat(identifiant="S1", titre="t", verdict="vert", motif="m", duree_s=1.0),),
    )
    dossier_rapport = tmp_path / "rapports" / rapport.horodatage
    dossier_rapport.mkdir(parents=True)

    chemin, carte = Carte(dossier).ecrire(
        rapport, dossier_rapport, racine=tmp_path, suivis=_SUIVIS, miroirs={}
    )

    assert chemin == dossier_rapport / carte_module.FICHIER_CARTE
    assert charger(chemin) == carte
    assert carte.scenarios["S1"].fonctions == {"maestro/api.py": ("f",)}


# ── Préparer : ce que `start.sh` exporte ──────────────────────────────────────


def test_preparer_remet_le_dossier_a_neuf_et_met_la_sonde_en_tete(tmp_path: Path) -> None:
    """Les relevés d'un passage précédent ne se mêlent pas à ceux-ci, et le
    `PYTHONPATH` existant est gardé derrière la sonde, sans la doubler."""
    perime = tmp_path / carte_module.DOSSIER_CARTE / sonde.DOSSIER_TRACES / "1-vieux.tsv"
    perime.parent.mkdir(parents=True)
    perime.write_text("S1\tf\tmaestro/api.py\tf\n", encoding="utf-8")
    deja = os.pathsep.join([str(carte_module.DOSSIER_SONDE), str(tmp_path / "lib")])

    variables = preparer(tmp_path, {"PYTHONPATH": deja})

    dossier = Path(variables[sonde.VARIABLE_DOSSIER])
    assert dossier == (tmp_path / carte_module.DOSSIER_CARTE).resolve()
    assert dossier.is_absolute(), "un processus lancé hors de la copie le lit tel quel"
    assert not perime.exists()
    assert (dossier / sonde.DOSSIER_TRACES).is_dir()
    assert variables["PYTHONPATH"].split(os.pathsep) == [
        str(carte_module.DOSSIER_SONDE),
        str(tmp_path / "lib"),
    ]


def test_la_commande_preparer_rend_une_variable_par_ligne(tmp_path: Path) -> None:
    sortie = io.StringIO()

    code = carte_module.main(["--preparer"], racine=tmp_path, sortie=sortie)

    assert code == carte_module.CODE_OK
    noms = [ligne.partition("=")[0] for ligne in sortie.getvalue().splitlines()]
    assert noms == [sonde.VARIABLE_DOSSIER, "PYTHONPATH"]


def test_le_dossier_de_la_sonde_est_celui_du_sitecustomize() -> None:
    """Ce que `--preparer` pose sur `PYTHONPATH` doit contenir un `sitecustomize`."""
    assert (carte_module.DOSSIER_SONDE / "sitecustomize.py").is_file()
    assert carte_module.DOSSIER_SONDE == DOSSIER_SONDE.resolve()


def test_start_sh_pose_la_carte_avant_l_api_et_seulement_pour_un_passage() -> None:
    """Le bloc qui exporte la sonde se joue sur `--etat-banc --rejouer`, **avant** que
    l'API démarre (elle en hérite, et tout ce qu'elle lance), et ne nomme aucune
    variable : les noms viennent de `--preparer`."""
    source = (RACINE / "scripts" / "controltower" / "start.sh").read_text(encoding="utf-8")
    preparer_ligne = source.index("-m maestro.scenarios.carte --preparer")
    api = source.index("nohup \"$PYTHON\" -m maestro.controltower.cli")
    assert preparer_ligne < api
    bloc = source[source.rindex("\nif ", 0, preparer_ligne) : preparer_ligne]
    assert '[ "$REJOUER" = 1 ] && [ "$DONNEES" = "banc" ]' in bloc
    assert sonde.VARIABLE_DOSSIER not in source, "le nom vit dans Python"
    assert "MAESTRO_BANC_CARTE" in source, "le passage sans carte se demande, et se dit"


_DEBUT_DE_LA_CARTE = 'if [ "$REJOUER" = 1 ] && [ "$DONNEES" = "banc" ]; then'


def _bloc_de_la_carte() -> str:
    """Le bloc de `start.sh` qui pose la carte, tel qu'il est écrit — jamais recopié."""
    lignes = (RACINE / "scripts" / "controltower" / "start.sh").read_text("utf-8").splitlines()
    debut = lignes.index(_DEBUT_DE_LA_CARTE)
    fin = next(i for i in range(debut, len(lignes)) if lignes[i] == "fi")
    return "\n".join(lignes[debut : fin + 1])


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash introuvable")
@pytest.mark.parametrize(
    ("eteinte", "attendu"),
    [("", r"C:\Une Copie\.maestro\carte|C:\Une Copie\sonde;D:\lib"), ("0", "|")],
)
def test_le_bloc_exporte_ce_que_preparer_rend_tel_quel(
    tmp_path: Path, eteinte: str, attendu: str
) -> None:
    """Ce que Python imprime sous Windows finit ses lignes en CRLF, et ses chemins ont
    des espaces : le bloc exporte la valeur entière, sans le `\\r`. `MAESTRO_BANC_CARTE=0`
    n'exporte rien, et le dit."""
    faux = tmp_path / "faux-python.sh"
    faux.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\r\\n' 'MAESTRO_CARTE_DOSSIER=C:\\Une Copie\\.maestro\\carte' "
        "'PYTHONPATH=C:\\Une Copie\\sonde;D:\\lib'\n",
        encoding="utf-8",
        newline="\n",
    )
    faux.chmod(0o755)
    script = (
        "set -euo pipefail\n"
        'DONNEES=banc REJOUER=1 PYTHON="$FAUX_PYTHON"\n'
        "unset MAESTRO_CARTE_DOSSIER PYTHONPATH\n"
        + _bloc_de_la_carte()
        + '\necho "${MAESTRO_CARTE_DOSSIER:-}|${PYTHONPATH:-}"\n'
    )
    environnement = {**os.environ, "FAUX_PYTHON": faux.as_posix()}
    environnement.pop("MAESTRO_BANC_CARTE", None)
    if eteinte:
        environnement["MAESTRO_BANC_CARTE"] = eteinte
    bash = shutil.which("bash")
    assert bash is not None
    fini = subprocess.run(  # noqa: S603 - bash, script écrit ici
        [bash, "-c", script],
        cwd=tmp_path,
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )

    assert fini.returncode == 0, fini.stderr
    assert fini.stdout.splitlines()[-1] == attendu
    assert ("éteinte" in fini.stdout) is bool(eteinte)
