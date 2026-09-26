"""L'analyse d'un projet existant, et l'outillage qu'elle recommande (#1030, docs/38).

Le lot 2/7 de #1020 avait livré sans tests (convention de découpage,
[docs/10 §5.1](../docs/10-workflow-git.md)) ; cette suite les rend. Elle garde ce
que le module **promet en toutes lettres** dans son en-tête, et c'est exactement
ce que le premier critère de #1035 demande : *l'analyse, lecture seule, bornes,
aucune exécution*.

Les trois promesses ne se gardent pas de la même façon, et le partage est le
sujet de ce fichier :

① **lecture seule** — la promesse se mesure **sur les appels**, pas sur le
   résultat. Un test qui comparerait l'arbre avant et après passerait au vert sur
   un module qui écrit puis efface, et qui aurait donc bel et bien touché au
   dossier de quelqu'un. `_SondeEcriture` intercepte `open`, `Path.open`,
   `os.mkdir`, `os.makedirs`, `os.remove` et `os.replace`, et elle est **prouvée
   sur un échantillon fautif** avant de servir de verdict ;
② **aucune exécution** — deux sondes, parce que la promesse a deux moitiés. Le
   **texte** des modules ne doit importer ni `subprocess` ni `os.system` (relevé
   par l'AST, prouvé lui aussi sur un source fautif), et l'**exécution** d'une
   analyse sur un vrai dépôt Git ne doit lancer aucun processus — le VCS est lu
   dans `.git/config` (#221), c'est la propriété qui permet de s'appuyer dessus
   sans rouvrir la question ;
③ **les bornes** — chacune est atteinte **pour de vrai** sur un arbre fabriqué, et
   le `Parcours` rendu nomme la troncature. Une borne qui ne se voit pas dans la
   réponse ferait lire un projet plus petit qu'il n'est.

S'y ajoute ce dont l'analyse répond devant le reste du chantier : le **périmètre
du projet** (ni `.env` ni `**/secrets/**` ouverts, docs/24 §2.5), le
**déterminisme** (deux analyses du même arbre rendent le même relevé — sans quoi
le cas « empreinte identique » de docs/38 §4.2 serait inatteignable), et la
**recommandation**, dont chaque entrée porte l'endroit du projet qui la justifie.

Ce que cette suite **ne** couvre pas, et où c'est couvert : le questionnaire d'un
projet neuf et la recommandation qu'il produit sont dans
[`tests/test_outillage_questionnaire.py`](./test_outillage_questionnaire.py)
(#1031) — les deux voies se rejoignent sur `recommander`, éprouvée ici sur des
constats lus et là-bas sur des réponses ; l'écriture et le manifeste sont dans
[`tests/test_outillage_generation.py`](./test_outillage_generation.py) (#1033) ;
la transmission aux agents dans
[`tests/test_outillage_contexte.py`](./test_outillage_contexte.py) (#1032).

Ni réseau, ni Redis, ni modèle : l'analyse est une lecture de fichiers. Le seul
test qui a besoin d'un vrai dépôt en monte un jetable et est sauté là où `git`
manque.
"""

from __future__ import annotations

import ast
import builtins
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from maestro.outillage import (
    Bornes,
    Constats,
    DossierScripts,
    Piece,
    analyser,
    recommander,
    resume,
)
from maestro.outillage.clients import Client
from maestro.outillage.detection import IGNORES_DEFAUT
from maestro.projets.modele import Perimetre
from maestro.projets.racine import RacineRefusee

GIT = shutil.which("git")

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

#: Les modules du paquet dont la promesse « aucune exécution » est écrite. Le
#: dossier entier, relevé au moment du test plutôt que listé : un module ajouté
#: demain doit tomber sous la même règle sans que personne ait à y penser.
PAQUET = Path(__file__).resolve().parents[1] / "maestro" / "outillage"


# --------------------------------------------------------------------------- #
# Arbres de projet fabriqués                                                    #
# --------------------------------------------------------------------------- #


def ecrire(racine: Path, chemin: str, contenu: str = "") -> Path:
    """Pose un fichier sous `racine`, dossiers parents compris."""
    cible = racine / chemin
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(contenu, encoding="utf-8")
    return cible


def projet_node(racine: Path, *, verrou: bool = True) -> Path:
    """Un projet Node ordinaire : manifeste, scripts déclarés, et son verrou."""
    ecrire(
        racine,
        "package.json",
        '{"name": "depensio", "scripts": {"test": "vitest run", '
        '"build": "next build", "lint": "eslint .", "dev": "next dev"}}',
    )
    if verrou:
        ecrire(racine, "package-lock.json", "{}")
    ecrire(racine, "src/app.ts", "export const x = 1;\n")
    ecrire(racine, "src/page.tsx", "export default function Page() {}\n")
    return racine


def projet_python(racine: Path) -> Path:
    """Un projet Python avec `pyproject.toml`, un verrou `uv` et un dossier de tests."""
    ecrire(
        racine,
        "pyproject.toml",
        '[project]\nname = "depensio"\n\n[tool.ruff]\nline-length = 99\n',
    )
    ecrire(racine, "uv.lock", "version = 1\n")
    ecrire(racine, "src/api.py", "def main() -> None: ...\n")
    ecrire(racine, "tests/test_api.py", "def test_ok() -> None: ...\n")
    return racine


# --------------------------------------------------------------------------- #
# ① Lecture seule — la sonde, prouvée avant de servir                           #
# --------------------------------------------------------------------------- #


class _SondeEcriture:
    """Intercepte tout ce par quoi un module pourrait écrire, et retient les lectures.

    Deux services en un, et c'est voulu : `ecritures` porte ce qui aurait touché
    au disque (la promesse de lecture seule), `lectures` porte ce qui a été
    **ouvert** (la promesse du périmètre — un `.env` non lu ne peut pas fuir).
    Une seconde sonde pour la seconde question aurait dû réintercepter les mêmes
    fonctions, et deux jeux de correctifs sur `builtins.open` ne se composent pas.

    Elle s'**arme** autour de l'appel observé (`with sonde.armee():`) plutôt que
    de vivre le test entier : l'arbre du projet se fabrique avec les mêmes
    fonctions qu'elle intercepte, et une sonde posée d'un bout à l'autre
    empêcherait de poser le projet qu'on veut analyser.

    Une écriture est **notée**, jamais levée : lever masquerait la suite du
    parcours et rendrait un verdict partiel là où on veut l'inventaire complet.
    Les verbes qui écrivent sans passer par `open` (`mkdir`, `write_text`) sont
    en revanche neutralisés — les laisser passer ferait de la sonde un témoin
    qui regarde sans empêcher, sur des tests dont c'est tout le sujet.
    """

    def __init__(self) -> None:
        self.ecritures: list[str] = []
        self.lectures: list[str] = []

    def _noter(self, chemin: Any, mode: str) -> None:
        if any(lettre in mode for lettre in ("w", "a", "x", "+")):
            self.ecritures.append(str(chemin))
        else:
            self.lectures.append(str(chemin))

    @contextmanager
    def armee(self) -> Iterator[_SondeEcriture]:
        """Arme la sonde le temps du bloc, et la retire ensuite."""
        vrai_open = builtins.open
        vrai_path_open = Path.open

        def open_note(fichier: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            self._noter(fichier, mode)
            return vrai_open(fichier, mode, *args, **kwargs)

        def path_open_note(soi: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            self._noter(soi, mode)
            return vrai_path_open(soi, mode, *args, **kwargs)

        with pytest.MonkeyPatch.context() as correctif:
            correctif.setattr(builtins, "open", open_note)
            correctif.setattr(Path, "open", path_open_note)
            correctif.setattr(os, "mkdir", self._interdit("os.mkdir"))
            correctif.setattr(os, "makedirs", self._interdit("os.makedirs"))
            correctif.setattr(os, "remove", self._interdit("os.remove"))
            correctif.setattr(os, "replace", self._interdit("os.replace"))
            correctif.setattr(Path, "mkdir", self._interdit_methode("Path.mkdir"))
            correctif.setattr(Path, "write_text", self._interdit_methode("Path.write_text"))
            correctif.setattr(Path, "write_bytes", self._interdit_methode("Path.write_bytes"))
            yield self

    def _interdit(self, nom: str) -> Callable[..., None]:
        def note(*args: Any, **kwargs: Any) -> None:
            self.ecritures.append(f"{nom}({args[0] if args else ''})")

        return note

    def _interdit_methode(self, nom: str) -> Callable[..., None]:
        def note(soi: Any, *args: Any, **kwargs: Any) -> None:
            self.ecritures.append(f"{nom}({soi})")

        return note


def test_la_sonde_d_ecriture_voit_un_module_qui_ecrit(tmp_path: Path) -> None:
    """L'échantillon fautif, **avant** le verdict : la sonde attrape une écriture.

    Sans cette moitié, un `assert sonde.ecritures == []` serait vert quel que
    soit le module observé — y compris un module qui écrit par un chemin que la
    sonde n'intercepte pas. C'est la règle du dépôt : une sonde se prouve sur un
    échantillon fautif avant de balayer.
    """
    sonde = _SondeEcriture()

    with sonde.armee():
        with open(tmp_path / "temoin.txt", "w", encoding="utf-8") as flux:
            flux.write("écrit")
        (tmp_path / "second.txt").write_text("écrit aussi", encoding="utf-8")
        (tmp_path / "un-dossier").mkdir()

    assert len(sonde.ecritures) == 3
    assert any("temoin.txt" in trace for trace in sonde.ecritures)
    assert any("Path.write_text" in trace for trace in sonde.ecritures)
    assert any("Path.mkdir" in trace for trace in sonde.ecritures)


def test_analyser_n_ecrit_rien_dans_le_projet(tmp_path: Path) -> None:
    """La promesse n° 1, mesurée sur les appels : aucune ouverture en écriture."""
    projet_python(projet_node(tmp_path))
    ecrire(tmp_path, "AGENTS.md", "# Dépensio\n")
    ecrire(tmp_path, "scripts/tests.sh", "pytest\n")
    sonde = _SondeEcriture()

    with sonde.armee():
        analyse = analyser(tmp_path, projet_id="prj-0000dead")

    assert sonde.ecritures == []
    assert analyse.parcours.fichiers_vus > 0
    assert sonde.lectures, "l'analyse doit bien avoir ouvert les manifestes qu'elle cite"


def test_analyser_ne_cree_aucun_dossier_dans_une_racine_vide(tmp_path: Path) -> None:
    """Le cas limite : un projet vide ne doit pas recevoir d'atelier en passant."""
    sonde = _SondeEcriture()

    with sonde.armee():
        analyse = analyser(tmp_path)

    assert sonde.ecritures == []
    assert list(tmp_path.iterdir()) == []
    assert analyse.resume == (
        "aucun langage, gestionnaire ni outil constaté dans les bornes de l'analyse"
    )


def test_un_projet_vide_le_dit_au_lieu_de_rendre_un_resume_vide() -> None:
    """Un résumé vide se lirait comme une analyse qui n'a pas tourné (docstring de `resume`)."""
    assert resume(Constats()) != ""
    assert "aucun" in resume(Constats())


# --------------------------------------------------------------------------- #
# ② Aucune exécution — le texte des modules, puis un vrai dépôt                  #
# --------------------------------------------------------------------------- #


def _imports_d_execution(source: str) -> set[str]:
    """Les noms d'exécution de processus importés ou appelés dans `source`.

    Relevé sur l'AST et non par `grep` : un `# subprocess` en commentaire ou le
    mot dans une docstring — il y en a, ce module s'en explique — ne doit pas
    faire rougir, et un `from subprocess import run` doit faire rougir autant
    qu'un `import subprocess`.
    """
    trouves: set[str] = set()
    arbre = ast.parse(source)
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            trouves |= {alias.name for alias in noeud.names if alias.name in _INTERDITS}
        elif isinstance(noeud, ast.ImportFrom) and noeud.module in _INTERDITS:
            trouves.add(noeud.module)
        elif isinstance(noeud, ast.Attribute) and noeud.attr in ("system", "popen", "spawnv"):
            cible = noeud.value
            if isinstance(cible, ast.Name) and cible.id == "os":
                trouves.add(f"os.{noeud.attr}")
    return trouves


#: Les modules dont la seule importation ouvrirait la porte à l'exécution d'un
#: processus. `os` n'en est pas : le paquet s'en sert pour `scandir`, et c'est
#: `os.system`/`os.popen` qui sont relevés nommément.
_INTERDITS = {"subprocess", "multiprocessing", "pty"}


def test_la_sonde_d_execution_voit_un_source_fautif() -> None:
    """L'échantillon fautif de la seconde sonde, avant qu'elle ne serve de verdict."""
    assert _imports_d_execution("import subprocess\n") == {"subprocess"}
    assert _imports_d_execution("from subprocess import run\n") == {"subprocess"}
    assert _imports_d_execution("import os\nos.system('rm -rf /')\n") == {"os.system"}
    assert _imports_d_execution('"""On parle de subprocess ici."""\nimport os\n') == set()


def test_aucun_module_du_paquet_n_importe_de_quoi_executer() -> None:
    """La promesse n° 2, sur le texte : le paquet entier, relevé et non listé."""
    fautifs = {
        module.name: trouves
        for module in sorted(PAQUET.glob("*.py"))
        if (trouves := _imports_d_execution(module.read_text(encoding="utf-8")))
    }

    assert fautifs == {}, f"« aucune exécution » (docs/38) est rompue par : {fautifs}"


@besoin_de_git
def test_analyser_un_depot_git_ne_lance_aucun_processus(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """La promesse n° 2, à l'exécution : la forge sort de `.git/config`, pas d'un `git remote`.

    Le dépôt est **réel** — c'est ce qui donne sa valeur au test : si le module
    passait par `git`, la sonde le dirait ici et nulle part ailleurs. Les trois
    portes de `subprocess` sont fermées ensemble : fermer `run` seule laisserait
    `Popen` ouverte, et c'est par là qu'un correctif « rapide » passerait.
    """
    racine = projet_python(tmp_path / "depot")
    assert GIT is not None
    subprocess.run([GIT, "init", "-b", "main"], cwd=racine, check=True, capture_output=True)
    subprocess.run(
        [GIT, "remote", "add", "origin", "git@github.com:une-org/depensio.git"],
        cwd=racine,
        check=True,
        capture_output=True,
    )

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("l'analyse a lancé un processus")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "check_output", refuse)

    analyse = analyser(racine, projet_id="prj-0000dead")

    assert analyse.constats.vcs is not None
    assert analyse.constats.vcs.type == "git"
    assert analyse.constats.forge is not None
    assert analyse.constats.forge.nom == "GitHub"
    assert analyse.constats.forge.chemin == ".git/config"


# --------------------------------------------------------------------------- #
# ③ Les bornes — atteintes pour de vrai, et dites dans la réponse                #
# --------------------------------------------------------------------------- #


def test_la_borne_de_fichiers_est_atteinte_et_nommee(tmp_path: Path) -> None:
    """`fichiers-max` : le parcours s'arrête, et le dit."""
    for index in range(12):
        ecrire(tmp_path, f"src/module{index}.py", "x = 1\n")

    analyse = analyser(tmp_path, bornes=Bornes(fichiers_max=5, ignores=IGNORES_DEFAUT))

    assert analyse.parcours.fichiers_vus == 5
    assert analyse.parcours.tronque
    assert analyse.parcours.troncatures == ("fichiers-max",)
    assert analyse.to_dict()["parcours"]["tronque"] is True


def test_la_borne_de_profondeur_arrete_la_descente_et_le_dit(tmp_path: Path) -> None:
    """`profondeur-max` : ce qui est plus bas n'est pas vu, et l'absence n'est pas muette."""
    ecrire(tmp_path, "a/b/c/d/profond.py", "x = 1\n")
    ecrire(tmp_path, "surface.py", "x = 1\n")

    analyse = analyser(tmp_path, bornes=Bornes(profondeur_max=2, ignores=IGNORES_DEFAUT))

    assert analyse.parcours.troncatures == ("profondeur-max",)
    assert analyse.parcours.profondeur_atteinte <= 2
    exemples = [langage.exemple for langage in analyse.constats.langages]
    assert exemples == ["surface.py"]


def test_un_dossier_ignore_n_est_pas_descendu_et_se_voit_dans_le_parcours(
    tmp_path: Path,
) -> None:
    """Sans cette borne, `node_modules` mangerait le budget et fausserait les parts.

    Le périmètre est volontairement **ouvert** sur `node_modules` : c'est la
    borne qu'on mesure, et le périmètre par défaut l'écarterait en amont — le
    test serait alors vert sans rien dire de `Bornes.ignores`.
    """
    projet_node(tmp_path)
    for index in range(40):
        ecrire(tmp_path, f"node_modules/paquet{index}/index.js", "module.exports = {};\n")

    analyse = analyser(tmp_path, perimetre=Perimetre(exclus=(".git",)))

    assert "node_modules" in analyse.parcours.ignores_rencontres
    assert not any("node_modules" in langage.exemple for langage in analyse.constats.langages)
    assert analyse.parcours.fichiers_vus < 40


def test_des_bornes_avec_une_liste_d_ignores_vide_la_veulent_vide(tmp_path: Path) -> None:
    """Le défaut retombe sur `IGNORES_DEFAUT` ; une liste **fournie** vide est respectée.

    C'est ce qui sépare « je n'ai rien dit » de « je veux tout voir », et
    l'analyse ne doit pas décider à la place du second.
    """
    ecrire(tmp_path, "node_modules/gauche/index.js", "module.exports = {};\n")
    ouvert = Perimetre(exclus=(".git",))

    vue = analyser(tmp_path, perimetre=ouvert, bornes=Bornes(ignores=()))
    cachee = analyser(tmp_path, perimetre=ouvert)

    assert vue.parcours.ignores_rencontres == ()
    assert vue.parcours.fichiers_vus == 1
    assert cachee.parcours.ignores_rencontres == ("node_modules",)
    assert cachee.parcours.fichiers_vus == 0


def test_les_bornes_voyagent_dans_la_reponse_avec_les_deux_promesses(tmp_path: Path) -> None:
    """`lecture_seule` et `execution` ne sont pas des réglages : ce sont les promesses, lisibles."""
    bornes = analyser(tmp_path).to_dict()["bornes"]

    assert bornes["lecture_seule"] is True
    assert bornes["execution"] == "aucune"
    assert bornes["fichiers_max"] == Bornes().fichiers_max
    assert ".git" in bornes["ignores"]


# --------------------------------------------------------------------------- #
# Le périmètre du projet — les deux gisements de secrets, jamais ouverts         #
# --------------------------------------------------------------------------- #


def test_ni_env_ni_secrets_ne_sont_ouverts_par_l_analyse(tmp_path: Path) -> None:
    """docs/24 §2.5, mesuré sur les **ouvertures** et pas sur le rendu.

    Vérifier que le secret n'apparaît pas dans la réponse ne suffirait pas : un
    module qui lirait `.env` puis n'en garderait rien aurait quand même chargé
    des clés en mémoire, et il suffirait d'un journal pour qu'elles en sortent.
    """
    projet_python(tmp_path)
    ecrire(tmp_path, ".env", "CLE_API=secret-a-ne-jamais-lire\n")
    ecrire(tmp_path, "secrets/production.json", '{"token": "secret-a-ne-jamais-lire"}')
    sonde = _SondeEcriture()

    with sonde.armee():
        analyse = analyser(tmp_path, perimetre=Perimetre())

    ouverts = [Path(chemin).name for chemin in sonde.lectures]
    assert ".env" not in ouverts
    assert "production.json" not in ouverts
    assert "secret-a-ne-jamais-lire" not in str(analyse.to_dict())


def test_un_perimetre_resserre_retire_ce_qu_il_nomme(tmp_path: Path) -> None:
    """Le périmètre déclaré s'applique **en plus** des dossiers ignorés.

    Le dossier retiré (`exemples`) n'est dans aucune des deux listes du dépôt :
    ce qui le fait disparaître ne peut donc être que le périmètre, et le test ne
    mesure pas une borne à la place de l'autre.
    """
    projet_python(tmp_path)
    ecrire(tmp_path, "exemples/copie.py", "x = 1\n")

    large = analyser(tmp_path, perimetre=Perimetre(exclus=(".git",)))
    etroit = analyser(tmp_path, perimetre=Perimetre(exclus=(".git", "exemples")))

    assert large.parcours.fichiers_vus == etroit.parcours.fichiers_vus + 1
    assert etroit.parcours.ignores_rencontres == ()


@pytest.mark.skipif(os.name == "nt", reason="lien symbolique : privilège non garanti sous Windows")
def test_un_lien_symbolique_n_est_jamais_suivi(tmp_path: Path) -> None:
    """Le vecteur d'évasion de docs/24 §2.5 : le lien est vu, jamais descendu."""
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    ecrire(dehors, "prive.py", "cle = 'secret'\n")
    racine = tmp_path / "projet"
    racine.mkdir()
    ecrire(racine, "src/app.py", "x = 1\n")
    (racine / "evasion").symlink_to(dehors, target_is_directory=True)

    analyse = analyser(racine)

    exemples = [langage.exemple for langage in analyse.constats.langages]
    assert exemples == ["src/app.py"]
    assert analyse.parcours.fichiers_vus == 1


# --------------------------------------------------------------------------- #
# Déterminisme — deux analyses du même arbre rendent le même relevé              #
# --------------------------------------------------------------------------- #


def test_deux_analyses_du_meme_projet_rendent_les_memes_constats(tmp_path: Path) -> None:
    """Sans cette propriété, « empreinte identique » (docs/38 §4.2) serait inatteignable.

    L'identifiant et la date changent — ce sont les deux seules choses qui
    doivent changer —, et c'est ce que l'assertion isole plutôt que de comparer
    les dictionnaires entiers.
    """
    projet_python(projet_node(tmp_path))

    premiere = analyser(tmp_path, projet_id="prj-0000dead").to_dict()
    seconde = analyser(tmp_path, projet_id="prj-0000dead").to_dict()

    assert premiere["id"] != seconde["id"]
    for cle in ("constats", "recommandation", "parcours", "resume", "bornes"):
        assert premiere[cle] == seconde[cle]


def test_l_identifiant_d_une_analyse_est_sa_reference_au_manifeste(tmp_path: Path) -> None:
    """`source_manifeste()` est la seule forme du fragment `source` côté analyse (docs/38 §4.1)."""
    analyse = analyser(projet_python(tmp_path), projet_id="prj-0000dead")

    source = analyse.source_manifeste()

    assert source == {
        "type": "analyse",
        "projet_id": "prj-0000dead",
        "reference": analyse.id,
        "resume": analyse.resume,
    }
    assert analyse.id.startswith("ana-")


# --------------------------------------------------------------------------- #
# Les constats — rien sans le chemin qui le prouve                               #
# --------------------------------------------------------------------------- #


def test_un_projet_node_rend_ses_commandes_declarees_avec_leur_extrait(tmp_path: Path) -> None:
    """Une commande sans son chemin serait une supposition bien tournée (cf. `Commande`)."""
    projet_node(tmp_path)

    constats = analyser(tmp_path).constats

    tester = constats.commande_de("tester")
    assert tester is not None
    assert tester.commande == "npm run test"
    assert tester.chemin == "package.json"
    assert tester.origine == "declaree"
    assert tester.extrait
    assert all(commande.chemin for commande in constats.commandes)


def test_le_verrou_decide_de_la_commande_d_installation(tmp_path: Path) -> None:
    """`npm ci` **avec** verrou, `npm install` sans : un outillage qui ne marche pas est pire."""
    avec = analyser(projet_node(tmp_path / "avec")).constats
    sans = analyser(projet_node(tmp_path / "sans", verrou=False)).constats

    installer_avec = avec.commande_de("installer")
    installer_sans = sans.commande_de("installer")
    assert installer_avec is not None and installer_avec.commande == "npm ci"
    assert installer_sans is not None and installer_sans.commande == "npm install"
    assert avec.gestionnaires[0].verrou == "package-lock.json"
    assert sans.gestionnaires[0].verrou is None


def test_le_verrou_python_decide_du_gestionnaire(tmp_path: Path) -> None:
    """`pyproject.toml` ne dit pas qui installe : c'est `uv.lock` qui tranche."""
    constats = analyser(projet_python(tmp_path)).constats

    assert [gestionnaire.nom for gestionnaire in constats.gestionnaires] == ["uv"]
    installer = constats.commande_de("installer")
    assert installer is not None
    assert installer.commande.startswith("uv ")


def test_une_commande_declaree_passe_devant_la_convention_de_l_outil(tmp_path: Path) -> None:
    """L'ordre vit dans `_ordonner`, une fois — et `commande_de` en dépend."""
    ecrire(tmp_path, "pyproject.toml", '[project]\nname = "x"\n\n[tool.pytest.ini_options]\n')
    ecrire(tmp_path, "src/api.py", "x = 1\n")
    ecrire(tmp_path, "tests/test_api.py", "def test_ok(): ...\n")

    constats = analyser(tmp_path).constats

    tests = [commande for commande in constats.commandes if commande.usage == "tester"]
    assert tests, "un projet Python avec un pyproject et des tests doit rendre une commande"
    assert tests[0].origine == "declaree"


def test_pytest_par_convention_ne_se_declenche_que_faute_de_mieux(tmp_path: Path) -> None:
    """Le filet : un projet qui déclare sa commande n'a pas besoin qu'on en devine une seconde."""
    ecrire(tmp_path, "src/api.py", "x = 1\n")
    ecrire(tmp_path, "tests/test_api.py", "def test_ok(): ...\n")

    constats = analyser(tmp_path).constats

    tester = constats.commande_de("tester")
    assert tester is not None
    assert tester.commande == "pytest"
    assert tester.origine == "convention"
    assert tester.chemin == "tests"


def test_le_dossier_de_scripts_est_constate_jamais_impose(tmp_path: Path) -> None:
    """docs/38 §3.4 : `bin/` gagne s'il existe, et `scripts` n'est qu'un défaut annoncé."""
    projet_python(tmp_path / "avec-bin")
    ecrire(tmp_path / "avec-bin", "bin/tests.sh", "pytest\n")

    avec = analyser(tmp_path / "avec-bin").constats.dossier_scripts
    sans = analyser(projet_python(tmp_path / "sans")).constats.dossier_scripts

    assert avec.chemin == "bin"
    assert avec.constate is True
    assert [piece.chemin for piece in avec.scripts] == ["bin/tests.sh"]
    assert sans.chemin == "scripts"
    assert sans.constate is False
    assert sans.scripts == ()


def test_l_outillage_deja_present_est_reconnu_avec_son_chemin(tmp_path: Path) -> None:
    """Reconnaître au lieu de dupliquer : c'est cette liste qui le permet (docs/38 §3.3)."""
    projet_python(tmp_path)
    ecrire(tmp_path, "AGENTS.md", "# Dépensio\n")
    ecrire(
        tmp_path,
        ".claude/skills/lancer-les-tests/SKILL.md",
        "---\nname: lancer-les-tests\n---\n",
    )
    ecrire(tmp_path, ".agents/skills/sans-skill-md/autre.md", "vide\n")

    present = {piece.chemin: piece.role for piece in analyser(tmp_path).constats.outillage_present}

    assert present["AGENTS.md"] == "instructions"
    assert present[".claude/skills/lancer-les-tests/SKILL.md"] == "skill"
    assert not any("sans-skill-md" in chemin for chemin in present)


def test_un_dossier_illisible_est_saute_jamais_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Analyser le projet de quelqu'un, c'est accepter qu'une partie soit fermée."""
    projet_python(tmp_path)
    ecrire(tmp_path, "ferme/interne.py", "x = 1\n")
    vrai_scandir = os.scandir

    def scandir_refuse(chemin: Any = ".") -> Any:
        if str(chemin).endswith("ferme"):
            raise PermissionError("dossier fermé")
        return vrai_scandir(chemin)

    monkeypatch.setattr(os, "scandir", scandir_refuse)

    analyse = analyser(tmp_path)

    assert analyse.parcours.fichiers_vus > 0
    assert not any("ferme/" in langage.exemple for langage in analyse.constats.langages)


def test_une_racine_absente_ou_qui_n_est_pas_un_dossier_est_refusee_motivee(
    tmp_path: Path,
) -> None:
    """Le même type de refus que le reste du paquet `projets` — donc le même 422 côté API."""
    with pytest.raises(RacineRefusee) as absente:
        analyser(tmp_path / "nulle-part")
    fichier = ecrire(tmp_path, "un-fichier.txt", "x")
    with pytest.raises(RacineRefusee) as pas_un_dossier:
        analyser(fichier)

    assert absente.value.motif == "dossier-absent"
    assert pas_un_dossier.value.motif == "pas-un-dossier"


# --------------------------------------------------------------------------- #
# La recommandation — chaque entrée avec l'endroit qui la justifie               #
# --------------------------------------------------------------------------- #


def test_chaque_entree_recommandee_porte_sa_raison_et_son_endroit(tmp_path: Path) -> None:
    """Une recommandation sans son endroit serait invérifiable ; avec lui, elle se conteste."""
    recommandation = analyser(projet_node(tmp_path)).recommandation

    assert recommandation.entrees
    for entree in recommandation.entrees:
        assert entree.raison
        assert entree.justification is not None
        assert entree.justification.chemin


def test_l_ordre_des_entrees_est_celui_de_l_arbre_de_docs_38(tmp_path: Path) -> None:
    """Instructions, les ponts qu'il faut, puis les skills : l'ordre de lecture d'un projet outillé.

    Depuis #1295 un pont ne s'écrit que pour un client qui en a besoin : les deux sont
    demandés ici par deux clients qui ne lisent pas `AGENTS.md` d'eux-mêmes.
    """
    constats = analyser(projet_node(tmp_path)).constats
    clients = (
        Client(cle="claude", libelle="Claude Code", version="2.1.200"),
        Client(cle="gemini", libelle="Gemini CLI", version="0.9.0"),
    )

    entrees = recommander(constats, clients).entrees

    assert [entree.chemin for entree in entrees[:3]] == [
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
    ]
    assert all(entree.type == "skill" for entree in entrees[3:] if entree.type != "script")


def test_sans_client_qui_le_demande_l_analyse_ne_recommande_aucun_pont(tmp_path: Path) -> None:
    """#1295 : `AGENTS.md` seul, et les deux ponts écartés avec leur raison — jamais d'office."""
    recommandation = analyser(projet_node(tmp_path)).recommandation

    assert not any(entree.type == "pont" for entree in recommandation.entrees)
    assert {e.nom for e in recommandation.ecartes if e.type == "pont"} == {
        "CLAUDE.md",
        "GEMINI.md",
    }


def test_un_agents_md_deja_ecrit_est_a_completer_pas_a_ecraser(tmp_path: Path) -> None:
    """docs/38 §4.2 : Maestro n'y écrirait qu'un bloc délimité."""
    projet_node(tmp_path)
    ecrire(tmp_path, "AGENTS.md", "# Écrit avant Maestro\n")

    entrees = {e.chemin: e for e in analyser(tmp_path).recommandation.entrees}

    assert entrees["AGENTS.md"].etat == "a-completer"
    assert "bloc" in entrees["AGENTS.md"].raison


def test_un_skill_deja_present_est_repris_avec_son_chemin_jamais_duplique(
    tmp_path: Path,
) -> None:
    """Un skill posé dans `.claude/skills/` par quelqu'un est un skill du projet (docs/38 §3.3)."""
    projet_node(tmp_path)
    ecrire(
        tmp_path,
        ".claude/skills/lancer-les-tests/SKILL.md",
        "---\nname: lancer-les-tests\ndescription: joue la suite\n---\n",
    )

    entrees = {e.nom: e for e in analyser(tmp_path).recommandation.entrees}

    assert entrees["lancer-les-tests"].etat == "deja-present"
    assert entrees["lancer-les-tests"].chemin == ".claude/skills/lancer-les-tests/SKILL.md"
    assert not any(
        entree.chemin.startswith(".agents/skills/lancer-les-tests")
        for entree in analyser(tmp_path).recommandation.entrees
    )


def test_un_script_du_projet_est_appele_par_le_skill_au_lieu_d_etre_reecrit(
    tmp_path: Path,
) -> None:
    """docs/38 §3.4 : le chemin est **depuis la racine**, la seule forme qui ne dépende de rien."""
    projet_python(tmp_path)
    ecrire(tmp_path, "scripts/test.sh", "pytest\n")

    entrees = {e.nom: e for e in analyser(tmp_path).recommandation.entrees}

    assert entrees["lancer-les-tests"].commandes[0] == "bash scripts/test.sh"
    assert entrees["test.sh"].etat == "deja-present"
    assert entrees["test.sh"].chemin == "scripts/test.sh"


def test_aucune_commande_n_est_generee_et_la_raison_est_nommee(tmp_path: Path) -> None:
    """docs/38 §3.5 : une décision, donc un écarté nommé — jamais un silence."""
    ecartes = analyser(projet_node(tmp_path)).recommandation.ecartes

    commandes = [ecarte for ecarte in ecartes if ecarte.type == "commande"]
    assert len(commandes) == 1
    assert "aucun format de commande" in commandes[0].raison
    entrees = analyser(tmp_path).recommandation.entrees
    assert not any(entree.type == "commande" for entree in entrees)


def test_un_skill_sans_commande_constatee_est_ecarte_avec_le_fait_du_projet(
    tmp_path: Path,
) -> None:
    """« Pas de skill de tests » doit se lire comme un fait du projet, pas un oubli de Maestro."""
    ecrire(tmp_path, "src/app.ts", "export const x = 1;\n")

    ecartes = {e.nom: e.raison for e in analyser(tmp_path).recommandation.ecartes}

    assert "lancer-les-tests" in ecartes
    assert "aucune commande" in ecartes["lancer-les-tests"]


def test_recommander_ne_touche_pas_au_disque_et_se_juge_sur_des_constats() -> None:
    """La frontière du module : `recommander` ne reçoit que des `Constats`.

    C'est elle qui rend la recommandation éprouvable sans projet réel — et c'est
    aussi elle qui fait que #1031 (un projet neuf, sans disque) peut rendre la
    **même** recommandation par la même fonction.
    """
    constats = Constats(
        dossier_scripts=DossierScripts(
            chemin="scripts",
            constate=True,
            scripts=(Piece(nom="tests.sh", chemin="scripts/tests.sh", role="tester"),),
        )
    )
    sonde = _SondeEcriture()

    with sonde.armee():
        recommandation = recommander(constats)

    assert sonde.ecritures == []
    assert sonde.lectures == []
    noms = [entree.nom for entree in recommandation.entrees]
    assert noms[0] == "AGENTS.md"
    assert "lancer-les-tests" in noms
