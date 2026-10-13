"""La vérification d'une tâche en place ne laisse rien dans la racine de la personne (#1496).

Sur un projet **non versionné** (#839), la vérification d'une tâche (#1177) jouait ses
commandes dans la racine de la personne, qui est l'espace de travail de l'agent : ce
qu'elles y écrivaient y restait (`.pytest_cache/`, un rapport JUnit), un outil qui
réécrit en place (`ruff check --fix`) retouchait un fichier de la personne, ces restes
étaient comptés à l'agent, et l'agent qui corrigeait ne retrouvait pas ses fichiers.

Elle joue désormais dans un **miroir** de la racine, tenu par l'espace en place
(`maestro.sandbox.verification.MiroirDeVerification`) : rien n'est écrit dans la
racine, donc rien n'y est à défaire — la contrainte du ticket (« Maestro n'efface ni
ne réécrit jamais, dans la racine de la personne, ce qu'il n'y a pas lui-même écrit »)
tient par construction.

Trois étages :

- la **reproduction** du ticket, sur de vrais outils (`python -m pytest`, `ruff check
  --fix`) joués par le bash des agents — seuls l'agent et la réponse du vérificateur
  sont doublés ;
- le **miroir** lui-même : ce qu'il porte, comment il se resynchronise, ses bornes,
  ses liens ;
- la **recette** : les preuves disent les chemins de la racine, et une copie
  impossible rend le contrôle « non joué », jamais rejoué dans la racine.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.engine.verification import (
    SYSTEME,
    LivraisonNonVerifiee,
    Recette,
    VerificateurTaches,
)
from maestro.fichiers import retirer_arbre
from maestro.orchestrator.schema import Task
from maestro.projets.modele import Perimetre, Projet
from maestro.providers.base import ModelProvider
from maestro.sandbox import LieuSansTrace, espace_de_travail
from maestro.sandbox import verification as execution
from maestro.sandbox.en_place import portee_de
from maestro.sandbox.verification import CopieImpossible, MiroirDeVerification

#: L'interpréteur des doubles de joueur : jamais lancé, il n'a qu'à exister.
_INTERPRETE = ("bash", "-c")


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` : sous Windows, `AppData` refuserait les racines."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def _arborescence(racine: Path) -> list[str]:
    """Les fichiers de `racine`, en chemins relatifs POSIX, triés."""
    return sorted(p.relative_to(racine).as_posix() for p in racine.rglob("*") if p.is_file())


def _contenus(racine: Path) -> dict[str, bytes]:
    """Chaque fichier de `racine` et ses octets — ce qu'un relevé avant/après compare."""
    return {relatif: (racine / relatif).read_bytes() for relatif in _arborescence(racine)}


def _projet(racine: Path) -> Projet:
    return Projet(
        id="prj-0000beef",
        nom="Calculs",
        racine=racine.as_posix(),
        vcs=None,
        perimetre=Perimetre(),
    )


# --------------------------------------------------------------------------- #
# La reproduction du ticket : de vrais outils, joués par le bash des agents
# --------------------------------------------------------------------------- #

_APP_DE_LA_PERSONNE = "import os\nimport sys\n\nprint('appli de la personne')\n"
_LIVRAISONS = (
    {
        "somme.py": "import os\n\n\ndef somme(a, b):\n    return a - b\n",
        "test_somme.py": (
            "from somme import somme\n\n\ndef test_somme():\n    assert somme(2, 3) == 5\n"
        ),
    },
    {"somme.py": "import os\n\n\ndef somme(a, b):\n    return a + b\n"},
)
_CONTROLES = {
    "controles": [
        {
            "critere": "les tests passent",
            "commande": "python -m pytest -q --junitxml=.rapports/junit.xml",
        },
        {"critere": "le code passe le linter", "commande": "ruff check --fix ."},
    ]
}


class _AgentEtVerificateur(ModelProvider):
    """L'agent livre `_LIVRAISONS` session après session ; le vérificateur rend `_CONTROLES`."""

    name = "repro-en-place"

    def __init__(self) -> None:
        self.trouve: list[dict[str, str]] = []
        self.laisse: list[dict[str, str]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        assert system_prompt == SYSTEME
        return json.dumps(_CONTROLES)

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, **_):
        espace = Path(workspace)
        self.trouve.append(_lire(espace, ("somme.py", "test_somme.py")))
        for chemin, contenu in _LIVRAISONS[len(self.trouve) - 1].items():
            (espace / chemin).write_text(contenu, encoding="utf-8")
        self.laisse.append(_lire(espace, ("somme.py", "test_somme.py")))
        return f"Livré (session {len(self.trouve)})."


def _lire(racine: Path, chemins: tuple[str, ...]) -> dict[str, str]:
    return {c: (racine / c).read_text(encoding="utf-8") for c in chemins if (racine / c).is_file()}


@pytest.fixture
def run_en_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Le run du ticket : une livraison fausse, vérifiée, corrigée, revérifiée — en place."""
    interprete = execution.interprete()
    if interprete is None:  # pragma: no cover — poste sans bash
        pytest.skip("aucun bash sur ce poste")
    # Les outils du projet sont ceux de l'interpréteur qui joue la suite : `python`
    # et `ruff` viennent de son répertoire, en tête du PATH que bash hérite.
    outils = Path(sys.executable).parent
    monkeypatch.setenv("PATH", f"{outils}{os.pathsep}{os.environ['PATH']}")
    if shutil.which("ruff") is None:  # pragma: no cover — dépendances de dev absentes
        pytest.skip("ruff n'est pas installé à côté de l'interpréteur")
    racine = tmp_path / "projets" / "calculs"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text(_APP_DE_LA_PERSONNE, encoding="utf-8")
    (racine / "notes-de-la-personne.md").write_text("# Mes notes\n", encoding="utf-8")
    avant = _arborescence(racine)
    projet = _projet(racine)
    tache = Task(
        id="somme",
        titre="Écrire la fonction somme et son test",
        description="Critères de réussite : les tests passent ; le code passe le linter.",
        competences_requises=("backend",),
        format_sortie="somme.py et test_somme.py",
    )
    double = _AgentEtVerificateur()
    recette = Recette(
        verificateur=VerificateurTaches(double, interprete=interprete),
        tache=tache,
        modele="double",
        portee_de=lambda espace: portee_de(espace, projet),
    )
    issue = asyncio.run(
        AgentRuntime(double, DEVELOPER_PROFILE).execute(
            tache.description, projet=projet, tache_id=tache.id, on_livraison=recette
        )
    )
    return racine, issue, double, avant, recette


@pytest.mark.commandes_jouees
def test_la_verification_ne_laisse_rien_dans_la_racine(run_en_place) -> None:
    """Ni `.pytest_cache/`, ni `.ruff_cache/`, ni le rapport JUnit : le travail de l'agent, seul."""
    racine, _issue, _double, avant, recette = run_en_place

    restes = [
        chemin
        for chemin in _arborescence(racine)
        if chemin not in avant and chemin not in {"somme.py", "test_somme.py"}
    ]
    assert restes == []
    # Et la vérification a bien joué : la seconde livraison tient, critère par critère.
    assert recette.livraisons == 2
    assert recette.dernier is not None and recette.dernier.tenue


@pytest.mark.commandes_jouees
def test_la_verification_ne_reecrit_pas_un_fichier_de_la_personne(run_en_place) -> None:
    """`ruff check --fix` a réécrit `src/app.py` dans le miroir, jamais dans la racine."""
    racine, _issue, _double, _avant, _recette = run_en_place

    assert (racine / "src" / "app.py").read_text(encoding="utf-8") == _APP_DE_LA_PERSONNE


@pytest.mark.commandes_jouees
def test_les_restes_de_la_verification_ne_sont_pas_comptes_a_l_agent(run_en_place) -> None:
    """Les fichiers produits de la tâche — ce que lisent le juge et le récit — sont les siens."""
    _racine, issue, _double, _avant, _recette = run_en_place

    assert sorted(f.chemin for f in issue.fichiers) == ["somme.py", "test_somme.py"]


@pytest.mark.commandes_jouees
def test_l_agent_qui_corrige_retrouve_ses_fichiers(run_en_place) -> None:
    """Son `somme.py` garde son `import os` : la vérification ne l'a pas retouché entre-temps."""
    _racine, _issue, double, _avant, _recette = run_en_place

    assert len(double.trouve) == 2
    assert double.trouve[1] == double.laisse[0]


_SERVEUR = """\
import http.server, os, pathlib

reglages = pathlib.Path(".env").read_text(encoding="utf-8").splitlines()
message = dict(ligne.split("=", 1) for ligne in reglages if "=" in ligne)["MESSAGE"]
pathlib.Path("serveur.log").write_text("démarré\\n", encoding="utf-8")


class Reponse(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))

    def log_message(self, *_):
        pass


port = int(os.environ["MAESTRO_PORT"])
http.server.HTTPServer(("127.0.0.1", port), Reponse).serve_forever()
"""

_CONTROLE_SERVICE = {
    "controles": [
        {
            "critere": "le service répond le message de son .env",
            "commande": "python serveur.py",
            "sonde": "curl -s http://127.0.0.1:$MAESTRO_PORT/ | grep -q bonjour-de-la-racine",
        }
    ]
}


class _VerificateurDuService(ModelProvider):
    """Le vérificateur rend un contrôle de démarrage sondé (#1432) — rien d'autre."""

    name = "verificateur-service"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return json.dumps(_CONTROLE_SERVICE)

    async def run_agent(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("le vérificateur ne lance pas d'agent")


@pytest.mark.commandes_jouees
def test_un_service_demarre_et_sonde_dans_le_miroir_lit_son_env_et_ne_laisse_rien(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le verdict ne change pas de sens : un service démarré par la vérification (#1432)
    trouve son `.env` dans le miroir, répond à sa sonde sur le port que Maestro lui
    réserve (#1487) — et ce qu'il écrit là où il tourne n'atteint pas la racine."""
    interprete = execution.interprete()
    if interprete is None:  # pragma: no cover — poste sans bash
        pytest.skip("aucun bash sur ce poste")
    outils = Path(sys.executable).parent
    monkeypatch.setenv("PATH", f"{outils}{os.pathsep}{os.environ['PATH']}")
    if shutil.which("curl") is None:  # pragma: no cover — poste sans curl
        pytest.skip("curl n'est pas installé sur ce poste")
    racine = tmp_path / "projets" / "service"
    racine.mkdir(parents=True)
    (racine / ".env").write_text("MESSAGE=bonjour-de-la-racine\n", encoding="utf-8")
    (racine / "serveur.py").write_text(_SERVEUR, encoding="utf-8")
    projet = _projet(racine)
    tache = Task(
        id="service",
        titre="Écrire le service",
        description="Critère de réussite : le service répond le message de son .env.",
        competences_requises=("backend",),
        format_sortie="serveur.py",
    )
    recette = Recette(
        verificateur=VerificateurTaches(_VerificateurDuService(), interprete=interprete),
        tache=tache,
        modele="double",
        portee_de=lambda espace: portee_de(espace, projet),
    )

    with espace_de_travail(projet, tache_id=tache.id) as ws:
        avant = _contenus(racine)
        with ws.sans_trace() as lieu:
            assert lieu.chemin is not None
            retour = asyncio.run(recette(ws.path, "Livré.", (), lieu=lieu))
            assert (lieu.chemin / "serveur.log").is_file()
        assert _contenus(racine) == avant

    assert retour is None, retour
    assert recette.dernier is not None and recette.dernier.tenue
    assert not (racine / "serveur.log").exists()


# --------------------------------------------------------------------------- #
# Le miroir : ce qu'il porte, comment il suit la racine, ses bornes, ses liens
# --------------------------------------------------------------------------- #


def _racine_peuplee(tmp_path: Path) -> Path:
    """Une racine de projet web plausible : sources, dépendances, secret, atelier."""
    racine = tmp_path / "projets" / "boutique"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.ts").write_text("export const a = 1\n", encoding="utf-8")
    (racine / "README.md").write_text("# Boutique\n", encoding="utf-8")
    (racine / "ancien.txt").write_text("à retirer\n", encoding="utf-8")
    (racine / ".env").write_text("CLE=secrète\n", encoding="utf-8")
    (racine / "node_modules" / "vite").mkdir(parents=True)
    (racine / "node_modules" / "vite" / "index.js").write_text("module.exports = 1\n", "utf-8")
    (racine / ".maestro" / "t1").mkdir(parents=True)
    (racine / ".maestro" / "t1" / "brouillon.md").write_text("essai\n", encoding="utf-8")
    (racine / "vide").mkdir()
    return racine


def test_le_miroir_ne_nait_qu_a_la_premiere_synchronisation(tmp_path: Path) -> None:
    """Une tâche qu'on ne vérifie pas ne copie rien : le miroir naît quand on s'en sert."""
    racine = _racine_peuplee(tmp_path)
    miroir = MiroirDeVerification(racine)

    assert miroir.chemin is None
    chemin = miroir.synchroniser()
    try:
        assert miroir.chemin == chemin and chemin.is_dir()
    finally:
        miroir.fermer()
    assert not chemin.exists()


def test_le_miroir_porte_toute_la_racine_dependances_et_env_compris(tmp_path: Path) -> None:
    """La vérification juge dans l'environnement de l'agent : `node_modules`, `.env`, l'atelier.

    Les dépendances sont une **vraie copie** — ni lien, ni lien dur : une écriture dans
    le miroir (le cache de Vite dans `node_modules/.vite`) n'atteint pas la racine.
    """
    racine = _racine_peuplee(tmp_path)
    avant = _contenus(racine)
    miroir = MiroirDeVerification(racine)
    try:
        chemin = miroir.synchroniser()
        assert _contenus(chemin) == avant
        assert (chemin / "vide").is_dir()
        dependance = chemin / "node_modules" / "vite" / "index.js"
        assert not os.path.samefile(dependance, racine / "node_modules" / "vite" / "index.js")
        dependance.write_text("réécrit par la vérification\n", encoding="utf-8")
        (chemin / "node_modules" / ".vite").mkdir()
        (chemin / "node_modules" / ".vite" / "deps.json").write_text("{}", encoding="utf-8")
    finally:
        miroir.fermer()

    assert _contenus(racine) == avant


def test_le_miroir_suit_la_racine_et_oublie_ce_que_la_verification_y_a_laisse(
    tmp_path: Path,
) -> None:
    """À chaque vérification, le miroir est la racine à cet instant — dans un seul sens.

    Ce que la vérification précédente a écrit dans le miroir en est retiré, ce qu'elle
    y a réécrit revient à la version de la racine ; ce que l'agent a changé dans la
    racine depuis entre dans le miroir. La racine, elle, n'est jamais touchée.
    """
    racine = _racine_peuplee(tmp_path)
    miroir = MiroirDeVerification(racine)
    try:
        chemin = miroir.synchroniser()
        # Ce qu'une vérification laisse dans le miroir…
        (chemin / ".pytest_cache" / "v").mkdir(parents=True)
        (chemin / ".pytest_cache" / "v" / "lastfailed").write_text("{}", encoding="utf-8")
        (chemin / "src" / "app.ts").write_text("export const a = 1;\n", encoding="utf-8")
        (chemin / "README.md").unlink()
        # …et ce que l'agent change dans la racine pendant sa correction.
        (racine / "src" / "app.ts").write_text("export const a = 2\n", encoding="utf-8")
        (racine / "ancien.txt").unlink()
        (racine / "src" / "nouveau.ts").write_text("export {}\n", encoding="utf-8")
        attendu = _contenus(racine)

        assert miroir.synchroniser() == chemin
        assert _contenus(chemin) == attendu
    finally:
        miroir.fermer()

    assert _contenus(racine) == attendu


def test_un_fichier_reecrit_a_l_identique_dans_le_miroir_revient_quand_meme(
    tmp_path: Path,
) -> None:
    """Le miroir compare ses propres empreintes, pas seulement celles de la racine.

    Un formateur qui réécrit un fichier du miroir sans changer sa taille (`a - b` en
    `a + b`) le laisse différent de la racine : la racine n'a pas bougé, le miroir si.
    """
    racine = _racine_peuplee(tmp_path)
    miroir = MiroirDeVerification(racine)
    try:
        chemin = miroir.synchroniser()
        cible = chemin / "src" / "app.ts"
        cible.write_text("export const b = 1\n", encoding="utf-8")
        etat = cible.stat()
        os.utime(cible, ns=(etat.st_atime_ns, etat.st_mtime_ns + 7_000_000_000))

        miroir.synchroniser()

        assert cible.read_text(encoding="utf-8") == "export const a = 1\n"
    finally:
        miroir.fermer()


def test_un_miroir_au_dela_de_ses_bornes_est_impossible(tmp_path: Path) -> None:
    """Une vérification ne vaut pas la copie d'un disque : la borne se dit, rien n'est joué."""
    racine = _racine_peuplee(tmp_path)

    trop_de_fichiers = MiroirDeVerification(racine, fichiers_max=2)
    with pytest.raises(CopieImpossible) as exc:
        trop_de_fichiers.synchroniser()
    assert exc.value.motif == "trop-de-fichiers"
    trop_de_fichiers.fermer()

    trop_lourd = MiroirDeVerification(racine, octets_max=8)
    with pytest.raises(CopieImpossible) as exc:
        trop_lourd.synchroniser()
    assert exc.value.motif == "trop-lourd"
    trop_lourd.fermer()


def test_un_miroir_ne_nait_pas_dans_la_racine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un répertoire temporaire pointé dans le projet : le miroir y écrirait. Il ne naît pas."""
    racine = _racine_peuplee(tmp_path)
    dans_la_racine = racine / "tmp"
    dans_la_racine.mkdir()
    monkeypatch.setattr(
        "maestro.sandbox.verification.jetable",
        lambda prefixe: Path(os.path.realpath(dans_la_racine / "maestro-verif-x")),
    )
    (dans_la_racine / "maestro-verif-x").mkdir()

    with pytest.raises(CopieImpossible) as exc:
        MiroirDeVerification(racine).synchroniser()
    assert exc.value.motif == "dans-la-racine"


def _lien_possible(tmp_path: Path) -> bool:
    """Ce poste crée-t-il un lien symbolique ? (Windows : mode développeur ou administrateur.)"""
    essai = tmp_path / "essai-lien"
    try:
        essai.symlink_to(tmp_path)
    except (OSError, NotImplementedError):
        return False
    essai.unlink()
    return True


def test_un_lien_vers_la_racine_mene_dans_le_miroir(tmp_path: Path) -> None:
    """Un lien qui mène dans la racine — relatif comme `node_modules/.bin`, ou absolu — mène
    dans le miroir : une écriture à travers lui n'atteint jamais la racine."""
    if not _lien_possible(tmp_path):
        pytest.skip("ce poste ne crée pas de lien symbolique")
    racine = _racine_peuplee(tmp_path)
    (racine / "node_modules" / ".bin").mkdir()
    (racine / "node_modules" / ".bin" / "vite").symlink_to(Path("..") / "vite" / "index.js")
    (racine / "absolu").symlink_to(racine / "src" / "app.ts")
    avant = _contenus(racine)
    miroir = MiroirDeVerification(racine)
    try:
        chemin = miroir.synchroniser()
        for lien in (chemin / "node_modules" / ".bin" / "vite", chemin / "absolu"):
            assert lien.is_symlink()
            assert Path(os.path.realpath(lien)).is_relative_to(Path(os.path.realpath(chemin)))
        (chemin / "absolu").write_text("écrit à travers le lien\n", encoding="utf-8")
        miroir.synchroniser()
        assert (chemin / "absolu").is_symlink()
        assert (chemin / "src" / "app.ts").read_text(encoding="utf-8") == "export const a = 1\n"
    finally:
        miroir.fermer()

    assert _contenus(racine) == avant


def test_retirer_un_arbre_ne_touche_pas_ce_qu_un_lien_designe(tmp_path: Path) -> None:
    """Le retrait du miroir ne change pas les droits d'un fichier que l'un de ses liens désigne.

    Un `.venv/bin/python` mène à l'interpréteur de la personne, hors de la racine :
    rendre ses fichiers inscriptibles avant de les effacer ne doit pas l'atteindre.
    """
    if not _lien_possible(tmp_path):
        pytest.skip("ce poste ne crée pas de lien symbolique")
    dehors = tmp_path / "interpreteur"
    dehors.write_text("#!/bin/sh\n", encoding="utf-8")
    dehors.chmod(stat.S_IREAD)
    mode = dehors.stat().st_mode
    arbre = tmp_path / "arbre"
    (arbre / ".venv" / "bin").mkdir(parents=True)
    (arbre / ".venv" / "bin" / "python").symlink_to(dehors)

    assert retirer_arbre(arbre)
    assert dehors.stat().st_mode == mode
    dehors.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_l_espace_en_place_joue_sa_verification_ailleurs_que_dans_la_racine(
    tmp_path: Path,
) -> None:
    """`sans_trace` rend un lieu qui n'est pas la racine, le même d'une livraison à l'autre,
    et retiré quand l'espace se referme."""
    racine = _racine_peuplee(tmp_path)
    with espace_de_travail(_projet(racine), tache_id="t-1496") as ws:
        avant = _contenus(racine)  # l'atelier de la tâche vient de s'ouvrir
        with ws.sans_trace() as premier:
            assert premier.chemin is not None and premier.chemin != ws.path
            (premier.chemin / ".pytest_cache").mkdir()
            (premier.chemin / "src" / "app.ts").write_text("formaté\n", encoding="utf-8")
        with ws.sans_trace() as second:
            assert second.chemin == premier.chemin
            assert not (second.chemin / ".pytest_cache").exists()
            assert (second.chemin / "src" / "app.ts").read_text(encoding="utf-8") == (
                "export const a = 1\n"
            )
        assert _contenus(racine) == avant

    assert not premier.chemin.exists()


def test_une_copie_impossible_se_dit_au_lieu_de_jouer_dans_la_racine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Au-delà d'une borne, `sans_trace` rend un lieu vide et son motif — jamais la racine."""
    monkeypatch.setattr("maestro.sandbox.verification.FICHIERS_MAX_MIROIR", 2)
    racine = _racine_peuplee(tmp_path)
    with espace_de_travail(_projet(racine), tache_id="t-1496") as ws:
        with ws.sans_trace() as lieu:
            assert lieu.chemin is None
            assert "fichiers" in lieu.empechement


# --------------------------------------------------------------------------- #
# La recette : les preuves disent la racine, une copie impossible n'est pas jouée
# --------------------------------------------------------------------------- #


class _Verificateur(ModelProvider):
    """Ne fait que rendre les contrôles établis — un contrôle, une commande."""

    name = "verificateur-1496"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return json.dumps(
            {"controles": [{"critere": "les tests passent", "commande": "npm test"}]}
        )

    async def run_agent(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("le vérificateur ne lance pas d'agent")


def _recette(joueur) -> Recette:
    tache = Task(
        id="somme",
        titre="Écrire somme",
        description="Critères de réussite : les tests passent.",
        competences_requises=("backend",),
        format_sortie="somme.ts",
    )
    return Recette(
        verificateur=VerificateurTaches(_Verificateur(), joueur=joueur, interprete=_INTERPRETE),
        tache=tache,
        modele="double",
        portee_de=lambda espace: portee_de(espace, None),
    )


def _msys(chemin: Path) -> str:
    """`C:\\a\\b` → `/c/a/b` : le chemin tel que l'écrit un outil lancé par Git Bash."""
    texte = str(chemin)
    if len(texte) > 1 and texte[1] == ":":
        return "/" + texte[0].lower() + texte[2:].replace("\\", "/")
    return texte


def test_les_preuves_disent_les_chemins_de_la_racine_pas_ceux_du_miroir(
    tmp_path: Path,
) -> None:
    """L'agent lit la preuve et cherche le fichier qu'elle nomme : il doit exister pour lui."""
    racine = tmp_path / "projets" / "calculs"
    racine.mkdir(parents=True)
    miroir = tmp_path / "temp" / "maestro-verif-1" / "projet"
    miroir.mkdir(parents=True)
    joues: list[Path] = []

    def joueur(commande, cwd, *, interprete, delai_s):
        joues.append(Path(cwd))
        sortie = (
            f"FAIL {Path(cwd) / 'somme.test.ts'}\n"
            f"  at {Path(cwd).as_posix()}/somme.ts:2:10\n"
            f"  at {_msys(Path(cwd))}/somme.ts:2:10\n"
        )
        return execution.Execution(code=1, sortie=sortie, duree_s=0.1)

    retour = asyncio.run(
        _recette(joueur)(
            racine, "Livré.", (), lieu=LieuSansTrace(chemin=miroir)
        )
    )

    assert joues == [miroir]
    assert retour is not None
    assert str(miroir) not in retour and miroir.as_posix() not in retour
    assert _msys(miroir) not in retour
    assert str(racine / "somme.test.ts") in retour
    assert f"{racine.as_posix()}/somme.ts:2:10" in retour
    assert f"{_msys(racine)}/somme.ts:2:10" in retour


def test_une_copie_impossible_rend_le_controle_non_joue_jamais_dans_la_racine(
    tmp_path: Path,
) -> None:
    """Le contrôle n'est pas joué, avec son motif ; la livraison est gardée (#1432)."""
    racine = tmp_path / "projets" / "calculs"
    racine.mkdir(parents=True)
    joues: list[Path] = []

    def joueur(commande, cwd, *, interprete, delai_s):  # pragma: no cover — jamais appelé
        joues.append(Path(cwd))
        return execution.Execution(code=0, sortie="", duree_s=0.1)

    motif = "le projet compte plus de 2 fichiers : trop pour une copie de vérification."
    with pytest.raises(LivraisonNonVerifiee) as exc:
        asyncio.run(
            _recette(joueur)(
                racine,
                "Livré.",
                (),
                lieu=LieuSansTrace(chemin=None, empechement=motif),
            )
        )

    assert joues == []
    assert motif in exc.value.motif
