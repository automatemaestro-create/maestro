"""Tests de l'espace de travail dérivé du projet (ticket #224, EF-36, D2 — puis #839).

Couvre les critères de #224 qui tiennent encore : **worktree Git** sur une branche
dédiée quand le projet est versionné (branche jamais supprimée, hors de la
racine), une tâche sans `projet_id` gardant le `mkdtemp()` d'avant — et le régime
que #839 leur a substitué pour le projet **non versionné** : l'espace de travail
**est la racine** (rien n'est copié, rien n'est retiré), le recensement passe par
le périmètre, et ce que la copie garantissait par absence est garanti par refus
(`FrontiereEcriture`). La copie du périmètre, elle, n'existe plus — et ses tests
d'inclusion (`inclus`) avec elle : l'inclusion ne restreint plus aucun espace
dérivé (voir `maestro.sandbox.en_place`).

Les tests des lots de #703 sont différés à #707 ; ceux qui sont ici gardent la
seule logique **critique** du lot — la frontière d'écriture (EF-38), la
sérialisation d'un projet non versionné et la ligne de journal qui manquait.

Depuis #1448, ce que la vérification d'une tâche joue dans sa copie de travail
n'atteint pas sa branche, et l'agent qui corrige repart de son espace intact
(`Workspace.sans_trace`) — la racine d'un projet non versionné, elle, n'est jamais
défaite après coup.

Aucun réseau, aucun modèle : les tests qui ont besoin d'un vrai dépôt en montent
un jetable et sont sautés là où `git` manque ; les chemins d'échec de Git (binaire
absent, worktree refusé) sont joués sur un `subprocess.run` factice, donc couverts
partout.
"""

import asyncio
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.agents.store import AgentStore
from maestro.engine.executor import (
    ROLE_A_ASSIGNER,
    STATUT_ECRITURE_EN_PLACE,
    STATUT_ECRITURE_SANS_OBJET,
    STATUT_FUSION_NON_TENTEE,
    STATUT_PROJET_INTROUVABLE,
    LocalExecutor,
)
from maestro.orchestrator.schema import Task
from maestro.projets.modele import Perimetre, Projet, Vcs
from maestro.projets.racine import RacineRefusee, detecter_vcs
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import (
    DOSSIER_ATELIER,
    ConflitAvecLaBase,
    EspaceCopieDeTravail,
    EspaceProjetIndisponible,
    FrontiereEcriture,
    LieuSansTrace,
    branche_de_tache,
    conflit_avec_la_base,
    espace_de_travail,
    frontiere_de,
)
from maestro.sandbox.en_place import portee_de
from maestro.sandbox.projet import CONSIGNE_COPIE_DE_TRAVAIL
from maestro.telemetry import RunJournal

GIT = shutil.which("git")

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path`, comme dans les tests du socle (#221).

    Indispensable sous Windows : le `tmp_path` de pytest vit sous `AppData`, que
    `valider_racine` interdit à juste titre — sans cette isolation, toutes les
    racines de projet de ce fichier seraient refusées.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


# --------------------------------------------------------------------------- #
# Fabriques de projets
# --------------------------------------------------------------------------- #


def _arborescence(racine: Path) -> None:
    """Un projet plausible : du code, de la plomberie et deux gisements de secrets."""
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (racine / "README.md").write_text("# Démo\n", encoding="utf-8")
    (racine / ".env").write_text("CLE=secrète\n", encoding="utf-8")
    (racine / "secrets").mkdir()
    (racine / "secrets" / "cle.pem").write_text("---", encoding="utf-8")
    (racine / "node_modules").mkdir()
    (racine / "node_modules" / "paquet.js").write_text("module.exports={}", encoding="utf-8")
    (racine / "apps" / "web" / "node_modules").mkdir(parents=True)
    (racine / "apps" / "web" / "node_modules" / "profond.js").write_text("x", encoding="utf-8")
    (racine / "apps" / "web" / "page.tsx").write_text("export default null", encoding="utf-8")
    (racine / "services" / "api").mkdir(parents=True)
    (racine / "services" / "api" / ".env").write_text("JETON=zz\n", encoding="utf-8")
    (racine / "services" / "api" / "secrets").mkdir()
    (racine / "services" / "api" / "secrets" / "jeton.txt").write_text("zz", encoding="utf-8")


def _projet(racine: Path, *, vcs: Vcs | None = None, perimetre: Perimetre | None = None) -> Projet:
    """Un projet inerte pointant sur `racine` (le dépôt n'est pas sollicité ici)."""
    return Projet(
        id="prj-0000dead",
        nom="Démo",
        racine=racine.as_posix(),
        vcs=vcs,
        perimetre=perimetre if perimetre is not None else Perimetre(),
    )


def _projet_copie(tmp_path: Path) -> Projet:
    """Un projet **non versionné** posé sur le disque, avec son arborescence."""
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    _arborescence(racine)
    return _projet(racine)


def _git(racine: Path, *arguments: str) -> str:
    """Lance `git` dans `racine` et rend sa sortie — échoue le test si Git échoue."""
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


def _projet_git(tmp_path: Path, *, branche: str = "main") -> Projet:
    """Un projet **versionné** : dépôt jetable, un commit, VCS détecté comme en vrai."""
    racine = tmp_path / "projets" / "depensio-git"
    racine.mkdir(parents=True)
    _arborescence(racine)
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", f"refs/heads/{branche}")
    _git(racine, "add", "-A")
    _git(
        racine,
        "-c",
        "user.email=tests@maestro",
        "-c",
        "user.name=Tests",
        "commit",
        "--quiet",
        "-m",
        "socle",
    )
    return _projet(racine, vcs=detecter_vcs(racine))


# --------------------------------------------------------------------------- #
# Tâche sans projet : le comportement d'avant, au caractère près
# --------------------------------------------------------------------------- #


def test_sans_projet_l_espace_reste_le_repertoire_jetable() -> None:
    with espace_de_travail(None, tache_id="t1") as ws:
        chemin = ws.path
        assert chemin.is_dir()
        assert not any(chemin.iterdir())  # créé vide, comme avant #224
        (chemin / "livrable.md").write_text("fait", encoding="utf-8")
        assert [f.chemin for f in ws.produced_files()] == ["livrable.md"]
    assert not chemin.exists()


# --------------------------------------------------------------------------- #
# Projet non versionné : la racine elle-même, en place (#839)
# --------------------------------------------------------------------------- #


def test_projet_non_versionne_travaille_dans_sa_racine(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t1") as ws:
        assert ws.path.resolve() == racine.resolve()  # l'espace **est** la racine
        (ws.path / "NOUVEAU.md").write_text("livrable", encoding="utf-8")
        # Visible pendant que l'agent l'écrit, pas seulement à la fin.
        assert (racine / "NOUVEAU.md").read_text(encoding="utf-8") == "livrable"
        (ws.path / "src" / "app.py").write_text("print('réécrit')\n", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    # Rien n'est perdu à la fermeture de l'espace — le défaut mesuré par #568.
    assert (racine / "NOUVEAU.md").read_text(encoding="utf-8") == "livrable"
    assert (racine / "src" / "app.py").read_text(encoding="utf-8") == "print('réécrit')\n"
    # Le livrable, c'est ce que l'agent a écrit — pas le projet qu'on lui a prêté.
    assert sorted(produits) == ["NOUVEAU.md", "src/app.py"]


def test_le_recensement_ne_rend_que_ce_que_l_agent_a_ecrit_dans_le_perimetre(
    tmp_path: Path,
) -> None:
    projet = _projet_copie(tmp_path)
    with espace_de_travail(projet, tache_id="t1") as ws:
        # Ce qu'un `Bash` pourrait toucher hors périmètre ne ressort pas au rapport :
        # `.env`, `node_modules` et `secrets/` sont exclus à toute profondeur.
        (ws.path / ".env").write_text("CLE=changée\n", encoding="utf-8")
        (ws.path / "node_modules" / "ajout.js").write_text("x", encoding="utf-8")
        (ws.path / "apps" / "web" / "node_modules" / "b.js").write_text("y", encoding="utf-8")
        (ws.path / "services" / "api" / "secrets" / "neuf.txt").write_text("s", encoding="utf-8")
        (ws.path / "livrable.md").write_text("ok", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    assert produits == ["livrable.md"]


def test_la_plomberie_git_n_entre_pas_au_recensement(tmp_path: Path) -> None:
    racine = tmp_path / "projets" / "faux-depot"
    (racine / ".git").mkdir(parents=True)
    (racine / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (racine / "note.md").write_text("bonjour", encoding="utf-8")
    # Projet déclaré non versionné bien qu'un `.git` traîne : c'est `vcs` qui décide
    # du régime, et l'exclusion doit tenir quand même.
    with espace_de_travail(_projet(racine), tache_id="t1") as ws:
        (ws.path / ".git" / "index").write_bytes(b"\x00")
        (ws.path / "note.md").write_text("modifiée", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    assert produits == ["note.md"]


def test_le_recensement_ne_suit_aucun_lien_symbolique(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    (dehors / "cle.txt").write_text("secret du poste", encoding="utf-8")
    try:
        (Path(projet.racine) / "src" / "evasion").symlink_to(dehors, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - dépend des droits Windows
        pytest.skip("liens symboliques indisponibles sur ce poste")
    with espace_de_travail(projet, tache_id="t1") as ws:
        # Modifié derrière le lien : `rglob` le verrait, le parcours par le périmètre non.
        (dehors / "cle.txt").write_text("modifié", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]
    assert produits == []


def test_rien_n_est_retire_de_la_racine_meme_sur_exception(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    with pytest.raises(RuntimeError):
        with espace_de_travail(projet, tache_id="t1") as ws:
            (ws.path / "en-cours.md").write_text("à moitié", encoding="utf-8")
            raise RuntimeError("boum")
    # Le `rmtree` du `finally` est ce qui a effacé `squelette-p1` (#568) : plus jamais.
    assert (racine / "README.md").is_file()
    assert (racine / "en-cours.md").read_text(encoding="utf-8") == "à moitié"


# --------------------------------------------------------------------------- #
# L'atelier de la tâche : la racine garde le livrable, pas les brouillons (#944)
# --------------------------------------------------------------------------- #


def test_l_atelier_de_la_tache_est_ouvert_dans_la_racine(tmp_path: Path) -> None:
    """Créé et pas seulement nommé : un agent qui y `cd` ou y lance un script en a
    besoin, là où `Write` l'aurait créé au passage."""
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)

    with espace_de_travail(projet, tache_id="verification-qa") as ws:
        assert ws.atelier == f"{DOSSIER_ATELIER}/verification-qa"
        assert (racine / DOSSIER_ATELIER / "verification-qa").is_dir()


def test_l_atelier_n_entre_jamais_au_recensement(tmp_path: Path) -> None:
    """Le défaut G12 vu du rapport de run : un harnais de vérification rangé à
    l'atelier ne doit pas ressortir en livrable — sans quoi on n'aurait fait que
    déplacer le désordre du disque vers le rapport."""
    projet = _projet_copie(tmp_path)

    with espace_de_travail(projet, tache_id="t1") as ws:
        atelier = ws.path / ws.atelier
        (atelier / "verif.mjs").write_text("// harnais", encoding="utf-8")
        (atelier / "notes").mkdir()
        (atelier / "notes" / "essai.txt").write_text("brouillon", encoding="utf-8")
        (ws.path / "index.html").write_text("<!doctype html>", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]

    assert produits == ["index.html"]
    # Rien n'est effacé pour autant : ce qui a servi reste lisible, rangé à part.
    assert (Path(projet.racine) / DOSSIER_ATELIER / "t1" / "verif.mjs").is_file()


def test_l_atelier_d_une_tache_voisine_n_entre_pas_davantage(tmp_path: Path) -> None:
    """Les tâches d'un projet non versionné se succèdent dans la même racine :
    l'atelier de la précédente n'est pas le livrable de la suivante."""
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    (racine / DOSSIER_ATELIER / "t0").mkdir(parents=True)
    (racine / DOSSIER_ATELIER / "t0" / "vieux.txt").write_text("d'avant", encoding="utf-8")

    with espace_de_travail(projet, tache_id="t1") as ws:
        (racine / DOSSIER_ATELIER / "t0" / "vieux.txt").write_text("touché", encoding="utf-8")
        (ws.path / "livrable.md").write_text("ok", encoding="utf-8")
        produits = [f.chemin for f in ws.produced_files()]

    assert produits == ["livrable.md"]


def test_l_atelier_s_ecrit_sans_que_la_frontiere_le_refuse(tmp_path: Path) -> None:
    """L'atelier est **dans** la racine et hors des exclusions : c'est une adresse
    donnée, pas un refus de plus — l'inverse d'une exclusion de périmètre, qui
    vaut aussi à l'écriture."""
    projet = _projet_copie(tmp_path)
    frontiere = frontiere_de(Path(projet.racine), projet)

    assert frontiere is not None
    assert frontiere.refus("Write", {"file_path": f"{DOSSIER_ATELIER}/t1/verif.mjs"}) is None


def test_l_espace_nomme_son_atelier_a_l_agent(tmp_path: Path) -> None:
    """Le fond du défaut : rien ne disait à l'agent où ranger ce qui n'est pas le
    livrable. La consigne le dit, et dit aussi que la racine est celle de
    quelqu'un."""
    projet = _projet_copie(tmp_path)

    with espace_de_travail(projet, tache_id="module-audio") as ws:
        consigne = ws.consigne_espace()

    assert f"{DOSSIER_ATELIER}/module-audio/" in consigne
    assert "racine du projet de l'utilisateur" in consigne


def test_le_jetable_va_ou_la_portee_le_laisse_passer(tmp_path: Path) -> None:
    """#1348 : la consigne envoyait le jetable « dans le répertoire temporaire du
    système », et l'agent l'écrivait sous un nom fixe (`/tmp/avant.sha`) que la
    portée rend à une personne. Elle nomme désormais le geste qui reste à lui :
    un dossier qu'il crée par `mktemp -d` dans la commande même."""
    projet = _projet_copie(tmp_path)

    with espace_de_travail(projet, tache_id="t1") as ws:
        consigne = ws.consigne_espace()
        portee = portee_de(ws.path, projet)

    assert "mktemp -d" in consigne
    assert (
        portee.commande_hors_portee(
            'T=$(mktemp -d) && python app.py > "$T/sortie"; cat "$T/sortie"; rm -rf "$T"'
        )
        == ""
    )
    assert "sort du" in portee.commande_hors_portee("python app.py > /tmp/sortie")


def test_l_espace_jetable_ne_dit_rien_de_lui_meme(tmp_path: Path) -> None:
    """Un répertoire jetable disparaît avec la tâche et n'est le projet de
    personne : il n'a ni atelier ni branche à nommer, et son message de tâche est
    celui d'avant #944, au caractère près. (Le worktree, lui, dit où il est depuis
    #1399, et qu'il se commite depuis #1401.)"""
    with espace_de_travail(None, tache_id="t1") as jetable:
        assert jetable.consigne_espace() == ""


@besoin_de_git
def test_un_projet_versionne_n_a_pas_d_atelier(tmp_path: Path) -> None:
    """Son worktree est hors de la racine et sa branche porte tout : ce qu'un agent
    y laisse ne salit le projet de personne avant la fusion — et la consigne de sa
    copie de travail ne lui promet aucun atelier (#1401)."""
    projet = _projet_git(tmp_path)

    with espace_de_travail(projet, tache_id="t1") as ws:
        consigne = ws.consigne_espace()
        assert not (Path(projet.racine) / DOSSIER_ATELIER).exists()
    assert DOSSIER_ATELIER not in consigne


# --------------------------------------------------------------------------- #
# La copie de travail dit à l'agent où il est (#1399)
# --------------------------------------------------------------------------- #


@besoin_de_git
def test_la_copie_de_travail_dit_a_l_agent_qu_il_est_a_la_racine_du_projet_nomme(
    tmp_path: Path,
) -> None:
    """Le défaut de p5 : « Installer dans le dossier p5 », et l'agent travaillait dans
    une copie nommée d'après sa tâche, où aucun `p5` n'existe. Rien ne lui disait
    que son répertoire courant **est** ce projet — il l'a consigné comme une
    décision. La copie le dit désormais : le projet par son nom et son dossier, sa
    branche, et que ce que la tâche nomme du projet désigne ce répertoire."""
    projet = _projet_git(tmp_path)
    dossier = Path(projet.racine).name

    with espace_de_travail(projet, tache_id="socle-nextjs") as ws:
        consigne = ws.consigne_espace()
        # Les conditions du défaut : la copie ne porte pas le nom du dossier.
        assert ws.path.name != dossier

    assert "racine du projet « Démo »" in consigne
    assert f"`{projet.racine}`" in consigne
    assert f"« {dossier} »" in consigne
    assert "`maestro/socle-nextjs`" in consigne
    assert "sans y créer de sous-dossier" in consigne
    # « La racine du projet » des instructions du projet (AGENTS.md) est ce répertoire.
    assert "« la racine du projet »" in consigne
    # Le dossier d'origine n'est pas sur sa branche : il n'y écrit pas.
    assert "échapperait à ta branche" in consigne
    # Et ce qu'il laisse n'est pas perdu : commité, puis appliqué.
    assert "commité sur ta branche" in consigne


@besoin_de_git
def test_un_projet_qui_porte_le_nom_de_son_dossier_n_est_nomme_qu_une_fois(
    tmp_path: Path,
) -> None:
    """p5 s'appelait comme son dossier : le dire deux fois n'apprendrait rien."""
    projet = _projet_git(tmp_path)
    projet = replace(projet, nom=Path(projet.racine).name)

    with espace_de_travail(projet, tache_id="t1") as ws:
        consigne = ws.consigne_espace()

    assert consigne.count(f"« {projet.nom} »") == 2  # le projet, puis ce que la tâche nomme
    assert " ou « " not in consigne


@besoin_de_git
def test_le_message_de_la_tache_dit_ou_travaille_l_agent_d_un_projet_versionne(
    tmp_path: Path,
) -> None:
    """Le bout de chaîne : la consigne de la copie arrive dans le prompt de la tâche,
    seul endroit où l'agent la lit, et la tâche elle-même n'y est pas réécrite
    (aucune réécriture lexicale de ce qu'elle nomme)."""
    projet = _projet_git(tmp_path)
    fournisseur = _FournisseurEcrivain()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)
    tache = "Installer Next.js dans le dossier depensio-git."

    asyncio.run(runtime.execute(tache, projet=projet, tache_id="socle-nextjs"))

    (prompt,) = fournisseur.prompts
    assert tache in prompt
    assert "racine du projet « Démo »" in prompt
    assert "`maestro/socle-nextjs`" in prompt


def test_le_message_de_la_tache_porte_l_atelier(tmp_path: Path) -> None:
    """Dit dans le message de la **tâche** et non dans le playbook du rôle : le
    régime dépend du projet, pas de l'agent."""
    projet = _projet_copie(tmp_path)
    fournisseur = _FournisseurEcrivain()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)

    asyncio.run(runtime.execute("Corriger le calcul", projet=projet, tache_id="t1"))

    (prompt,) = fournisseur.prompts
    assert f"{DOSSIER_ATELIER}/t1/" in prompt


def test_sans_projet_le_message_de_la_tache_ne_gagne_rien(tmp_path: Path) -> None:
    """La contrepartie : une tâche sans projet compose le message d'avant."""
    fournisseur = _FournisseurEcrivain()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)

    asyncio.run(runtime.execute("Corriger le calcul", tache_id="t1"))

    (prompt,) = fournisseur.prompts
    assert DOSSIER_ATELIER not in prompt


def test_l_espace_dit_que_le_livrable_peut_etre_un_etat(tmp_path: Path) -> None:
    """#1149 : la racine promettait « ce que tu y laisses est le livrable », ce qui
    est une fausse prémisse pour une tâche qui **agit** — vider un dossier n'y
    laisse rien. La nuance se dit dans le message de la tâche, avec le reste du
    régime, et non dans un playbook de rôle qui ne sait pas quel projet il sert."""
    projet = _projet_copie(tmp_path)

    with espace_de_travail(projet, tache_id="vider") as ws:
        consigne = ws.consigne_espace()

    assert "agir" in consigne
    assert "état de cette racine après ton geste" in consigne


def test_l_espace_nomme_les_chemins_hors_perimetre(tmp_path: Path) -> None:
    """La frontière d'écriture refuse ces chemins aux outils de fichiers, mais
    `Bash` n'est pas analysé (docs/24 §2.5) — et « vide le dossier » se fait au
    shell. Les nommer est la seule garde qui vaille des deux côtés."""
    projet = _projet_copie(tmp_path)

    with espace_de_travail(projet, tache_id="vider") as ws:
        consigne = ws.consigne_espace()

    for motif in Perimetre().exclus:
        assert motif in consigne, f"exclusion non dite à l'agent : {motif}"
    assert "commande shell ne les atteint pas" in consigne


def test_les_exclusions_dites_sont_celles_du_projet_jamais_une_liste_recopiee(
    tmp_path: Path,
) -> None:
    """Dérivées du périmètre : une exclusion ajoutée par l'utilisateur arrive à
    l'agent sans une ligne de plus, et un périmètre sans exclusion n'ajoute rien."""
    racine = tmp_path / "projets" / "sur-mesure"
    racine.mkdir(parents=True)
    sur_mesure = _projet(racine, perimetre=Perimetre(exclus=("dossier/prive",)))

    with espace_de_travail(sur_mesure, tache_id="t1") as ws:
        consigne = ws.consigne_espace()
    assert "dossier/prive" in consigne
    assert ".git" not in consigne

    sans_exclusion = _projet(racine, perimetre=Perimetre(exclus=()))
    with espace_de_travail(sans_exclusion, tache_id="t1") as ws:
        consigne = ws.consigne_espace()
    assert "hors du périmètre du projet" not in consigne


def test_le_message_de_la_tache_porte_le_perimetre_exclu(tmp_path: Path) -> None:
    """Le bout de chaîne : ce que l'espace dit arrive bien dans le prompt de la
    tâche, comme l'atelier — c'est là, et nulle part ailleurs, que l'agent le lit."""
    projet = _projet_copie(tmp_path)
    fournisseur = _FournisseurEcrivain()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)

    asyncio.run(runtime.execute("Vider le dossier", projet=projet, tache_id="vider"))

    (prompt,) = fournisseur.prompts
    assert ".env" in prompt and ".git" in prompt


# --------------------------------------------------------------------------- #
# La frontière d'écriture du régime en place (#839, EF-38)
# --------------------------------------------------------------------------- #


def _frontiere(tmp_path: Path) -> tuple[Projet, FrontiereEcriture]:
    projet = _projet_copie(tmp_path)
    return projet, FrontiereEcriture.pour(projet.racine, projet.perimetre)


def test_la_frontiere_laisse_passer_une_ecriture_ordinaire(tmp_path: Path) -> None:
    projet, frontiere = _frontiere(tmp_path)
    racine = Path(projet.racine)
    assert frontiere.refus("Write", {"file_path": "src/nouveau.py", "content": ""}) is None
    assert frontiere.refus("Edit", {"file_path": str(racine / "README.md")}) is None
    assert frontiere.refus("Read", {"file_path": "src/app.py"}) is None
    # Un outil sans chemin n'a rien à confronter.
    assert frontiere.refus("Bash", {"command": "cat .env"}) is None


def test_un_appel_sans_l_argument_de_chemin_est_refuse(tmp_path: Path) -> None:
    # Renversé par #1304 : « rien à confronter, donc passe » était le trou par
    # lequel un CLI qui renommerait l'argument ouvrait la frontière entière.
    _, frontiere = _frontiere(tmp_path)
    for entree in ({}, "pas un objet"):
        motif = frontiere.refus("Write", entree)
        assert motif is not None and "illisible" in motif, entree


@pytest.mark.parametrize("chemin", ["../dehors.txt", "src/../../evasion.py"])
def test_la_frontiere_refuse_l_ecriture_hors_de_la_racine(tmp_path: Path, chemin: str) -> None:
    _, frontiere = _frontiere(tmp_path)
    motif = frontiere.refus("Write", {"file_path": chemin})
    assert motif is not None and "sort de la racine" in motif


def test_un_chemin_absolu_d_ailleurs_est_refuse_en_ecriture_pas_en_lecture(
    tmp_path: Path,
) -> None:
    _, frontiere = _frontiere(tmp_path)
    ailleurs = tmp_path / "ailleurs" / "note.txt"
    motif = frontiere.refus("Write", {"file_path": str(ailleurs)})
    assert motif is not None and "sort de la racine" in motif
    # Lire hors de la racine n'est pas le sujet du périmètre — c'était déjà vrai de la copie.
    assert frontiere.refus("Read", {"file_path": str(ailleurs)}) is None


@pytest.mark.parametrize(
    "chemin",
    [
        ".env",
        "services/api/.env",
        "secrets/cle.pem",
        "secrets/neuf.txt",  # n'existe pas encore : l'exclusion vaut avant l'écriture
        "node_modules/paquet.js",
        "apps/web/node_modules/lib/profond.js",  # sous un dossier exclu, à toute profondeur
        ".git/config",
    ],
)
def test_la_frontiere_refuse_les_chemins_exclus_en_lecture_comme_en_ecriture(
    tmp_path: Path, chemin: str
) -> None:
    _, frontiere = _frontiere(tmp_path)
    for outil in ("Write", "Edit", "Read"):
        motif = frontiere.refus(outil, {"file_path": chemin})
        assert motif is not None and "exclu du périmètre" in motif, (outil, chemin)


def test_la_frontiere_ne_suit_aucun_lien_symbolique(tmp_path: Path) -> None:
    projet, frontiere = _frontiere(tmp_path)
    racine = Path(projet.racine)
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    try:
        (racine / "evasion").symlink_to(dehors, target_is_directory=True)
        (racine / "raccourci").symlink_to(racine / "src", target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - dépend des droits Windows
        pytest.skip("liens symboliques indisponibles sur ce poste")
    # Vers l'extérieur : résolu avant comparaison, donc hors racine.
    motif = frontiere.refus("Write", {"file_path": "evasion/cle.txt"})
    assert motif is not None and "sort de la racine" in motif
    # Vers l'intérieur : refusé quand même — aucun lien n'est suivi, où qu'il mène.
    motif = frontiere.refus("Write", {"file_path": "raccourci/app.py"})
    assert motif is not None and "lien symbolique" in motif


#: Le nom que `mktemp -d` avait rendu à l'agent dans le refus relevé par #1406
#: (sessions S6 du banc, 24-27/09) : `…\Temp\tmp.Vwp2egGgSK\petit.html`.
NOM_MKTEMP = "tmp.Vwp2egGgSK"


def _temporaire(tmp_path: Path) -> Path:
    """Le temporaire du poste, **à côté** du projet — nommé `Temp`, comme sous Windows."""
    temporaire = tmp_path / "Temp"
    temporaire.mkdir(exist_ok=True)
    return temporaire


def test_la_frontiere_laisse_ecrire_dans_le_dossier_cree_par_mktemp(tmp_path: Path) -> None:
    """#1406 : le cadre envoie l'agent ranger ses brouillons dans un dossier qu'il
    crée par `mktemp -d` ; l'agent obéit, puis y écrit avec l'outil d'écriture,
    qui refusait — « sort de la racine du projet », dix fois sur les passages S6.
    Ce dossier est à lui pour l'écriture aussi, qu'on y écrive un fichier neuf ou
    sous un sous-dossier qui n'existe pas encore."""
    _, frontiere = _frontiere(tmp_path)
    etabli = _temporaire(tmp_path) / NOM_MKTEMP
    etabli.mkdir()

    for chemin in (etabli / "petit.html", etabli / "harnais" / "verif.mjs"):
        for outil in ("Write", "Edit"):
            assert frontiere.refus(outil, {"file_path": str(chemin)}) is None, (outil, chemin)


@pytest.mark.parametrize(
    ("relatif", "a_l_agent"),
    [
        (f"Temp/{NOM_MKTEMP}/petit.html", True),
        (f"Temp/{NOM_MKTEMP}/harnais/verif.mjs", True),
        ("ailleurs/note.txt", False),  # pas un nom de `mktemp`
        ("Temp/tmp.court/note.txt", False),  # pas le nom de `mktemp`
        ("Temp/maestro-dev-carnet-v0iwvq4r/note.txt", False),  # l'espace d'une autre tâche
        (f"Temp/{NOM_MKTEMP}/../note.txt", False),  # remonter hors de son dossier
        (f"projets/voisin/{NOM_MKTEMP}/note.txt", False),  # pas sous un temporaire
        ("projets/voisin/note.txt", False),  # le projet voisin
    ],
)
def test_la_portee_et_la_frontiere_appliquent_le_meme_predicat(
    tmp_path: Path, relatif: str, a_l_agent: bool
) -> None:
    """#1406 : la portée (qui juge le shell) ouvrait l'établi, la frontière (qui
    juge l'outil d'écriture) non — #1348 n'avait touché que la première. Les deux
    rendent désormais le même verdict sur le même chemin, ce qu'on écrit dans
    l'établi comme ce qui reste dehors."""
    projet, frontiere = _frontiere(tmp_path)
    portee = portee_de(Path(projet.racine), projet)
    (_temporaire(tmp_path) / NOM_MKTEMP).mkdir()
    chemin = tmp_path / relatif

    ecriture = frontiere.refus("Write", {"file_path": str(chemin)})
    shell = portee.commande_hors_portee(f'echo essai > "{chemin.as_posix()}"')

    assert (ecriture is None) is a_l_agent, ecriture
    assert (shell == "") is a_l_agent, shell


def test_l_etabli_ne_s_ouvre_par_aucun_lien_symbolique(tmp_path: Path) -> None:
    """Un lien reste refusé, où qu'il soit : sous l'établi (un raccourci vers le
    projet voisin), ou l'établi lui-même (un nom de `mktemp` posé sur le voisin).
    Le nom se lit sur le texte ; ce que le disque met derrière se vérifie."""
    _, frontiere = _frontiere(tmp_path)
    voisin = tmp_path / "projets" / "voisin"
    voisin.mkdir(parents=True)
    temporaire = _temporaire(tmp_path)
    etabli = temporaire / NOM_MKTEMP
    etabli.mkdir()
    try:
        (etabli / "raccourci").symlink_to(voisin, target_is_directory=True)
        (temporaire / "tmp.Faux0Faux0").symlink_to(voisin, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - dépend des droits Windows
        pytest.skip("liens symboliques indisponibles sur ce poste")

    for chemin in (etabli / "raccourci" / "note.txt", temporaire / "tmp.Faux0Faux0" / "note.txt"):
        motif = frontiere.refus("Write", {"file_path": str(chemin)})
        assert motif is not None and "lien symbolique" in motif, chemin
    assert not (voisin / "note.txt").exists()


def test_un_dossier_de_mktemp_qui_contient_la_racine_n_est_pas_un_etabli(
    tmp_path: Path,
) -> None:
    """Un projet posé dans un dossier `mktemp` : ce dossier était là avant la
    tâche, et ce qu'il porte à côté de la racine — un projet voisin — n'est pas à
    l'agent. Ni à l'écriture, ni au shell, qui effacerait sinon le projet entier
    avec son conteneur (`rm -rf /tmp/tmp.X`)."""
    conteneur = _temporaire(tmp_path) / NOM_MKTEMP
    racine = conteneur / "depensio"
    racine.mkdir(parents=True)
    _arborescence(racine)
    projet = _projet(racine)
    frontiere = FrontiereEcriture.pour(projet.racine, projet.perimetre)
    portee = portee_de(racine, projet)

    motif = frontiere.refus("Write", {"file_path": str(conteneur / "voisin" / "note.txt")})
    assert motif is not None and "sort de la racine" in motif
    assert "sort du" in portee.commande_hors_portee(f'rm -rf "{conteneur.as_posix()}"')
    assert "sort du" in portee.commande_hors_portee(f'rm -rf "{conteneur.as_posix()}/voisin"')


def test_la_frontiere_n_est_armee_que_sur_la_racine(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    assert frontiere_de(racine, projet) is not None
    assert frontiere_de(racine / ".." / racine.name, projet) is not None  # même racine, résolue
    # Un espace qui n'est pas la racine n'en reçoit aucune : l'armer y refuserait tout.
    assert frontiere_de(tmp_path / "ailleurs", projet) is None
    assert frontiere_de(racine, None) is None
    versionne = _projet(racine, vcs=Vcs(type="git", branche_base="main"))
    assert frontiere_de(tmp_path / "worktree", versionne) is None


def test_le_hook_du_fournisseur_refuse_sur_la_frontiere_meme_sans_politique(
    tmp_path: Path,
) -> None:
    from maestro.providers import claude as claude_mod

    _, frontiere = _frontiere(tmp_path)
    refus: list[tuple[str, str]] = []
    hook = claude_mod._hook_permissions(
        None, lambda outil, motif: refus.append((outil, motif)), frontiere=frontiere
    )

    def _joue(outil: str, chemin: str) -> object:
        return asyncio.run(
            hook({"tool_name": outil, "tool_input": {"file_path": chemin}}, "tu-1", None)
        )

    sortie = _joue("Write", ".env")
    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"  # type: ignore[index]
    assert refus and refus[0][0] == "Write" and "exclu du périmètre" in refus[0][1]
    # Une écriture ordinaire passe, et sans politique il n'y a rien d'autre à juger.
    assert _joue("Write", "src/ok.py") == {}
    assert _joue("Bash", "") == {}
    # Le dossier que l'agent a créé par `mktemp -d` aussi (#1406), et rien n'est tracé.
    etabli = _temporaire(tmp_path) / NOM_MKTEMP
    etabli.mkdir()
    assert _joue("Write", str(etabli / "petit.html")) == {}
    assert len(refus) == 1


# --------------------------------------------------------------------------- #
# Projet versionné : un worktree Git par tâche
# --------------------------------------------------------------------------- #


@besoin_de_git
def test_projet_versionne_monte_un_worktree_sur_une_branche_dediee(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        assert (ws.path / "src" / "app.py").is_file()  # l'agent voit le projet
        assert (ws.path / ".env").is_file()  # un worktree porte tout le suivi Git
        with pytest.raises(ValueError):  # …mais hors de la racine
            ws.path.resolve().relative_to(racine.resolve())
        branches = _git(racine, "worktree", "list")
        assert "maestro/t-1" in branches
    assert not ws.path.exists()


@besoin_de_git
def test_le_worktree_est_retire_mais_jamais_la_branche(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        (ws.path / "src" / "nouveau.py").write_text("x = 1\n", encoding="utf-8")
        _git(ws.path, "add", "-A")
        _git(
            ws.path,
            "-c",
            "user.email=tests@maestro",
            "-c",
            "user.name=Tests",
            "commit",
            "--quiet",
            "-m",
            "travail de la tâche",
        )
    assert not ws.path.exists()  # worktree retiré
    assert "maestro/t-1" in _git(racine, "branch", "--list", "maestro/t-1")  # branche gardée
    assert "src/nouveau.py" in _git(racine, "show", "--name-only", "--format=", "maestro/t-1")


@besoin_de_git
def test_une_tache_rejouee_retrouve_sa_branche(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        (ws.path / "trace.txt").write_text("premier passage\n", encoding="utf-8")
        _git(ws.path, "add", "-A")
        _git(
            ws.path,
            "-c",
            "user.email=tests@maestro",
            "-c",
            "user.name=Tests",
            "commit",
            "--quiet",
            "-m",
            "premier passage",
        )
    with espace_de_travail(projet, tache_id="t-1") as ws:
        # La branche n'ayant pas été supprimée, le second montage la reprend :
        # une tâche rejouée continue son travail au lieu de l'écraser.
        assert (ws.path / "trace.txt").read_text(encoding="utf-8") == "premier passage\n"


def _commite(espace: Path, message: str) -> None:
    _git(espace, "add", "-A")
    _git(
        espace,
        "-c",
        "user.email=tests@maestro",
        "-c",
        "user.name=Tests",
        "commit",
        "--quiet",
        "-m",
        message,
    )


@besoin_de_git
def test_un_redecoupage_part_de_la_branche_de_la_tache_qu_il_remplace(tmp_path: Path) -> None:
    """Ce qui est fait n'est pas refait (#1396) : le travail de `socle` est sous les pieds.

    Le run `da0a8ae6f1b2` redécoupait `socle-nextjs` sur des branches parties de
    la base : le travail réussi de `maestro/socle-nextjs` n'était plus là, et
    `npm install` comme le reste se refaisaient de zéro.
    """
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="socle") as ws:
        (ws.path / "package.json").write_text('{"name": "p5"}\n', encoding="utf-8")
        _commite(ws.path, "le socle, livré")

    with espace_de_travail(projet, tache_id="socle-r1-init", reprend="socle") as ws:
        assert (ws.path / "package.json").read_text(encoding="utf-8") == '{"name": "p5"}\n'

    # La branche du redécoupage descend de celle de la tâche, qui reste intacte.
    socle = _git(racine, "rev-parse", "maestro/socle").strip()
    assert _git(racine, "merge-base", "maestro/socle-r1-init", "maestro/socle").strip() == socle


@besoin_de_git
def test_sans_branche_a_reprendre_le_redecoupage_part_de_la_base(tmp_path: Path) -> None:
    """Une tâche jamais montée n'a pas de branche : on part de la base, comme avant."""
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="socle-r1-init", reprend="socle") as ws:
        assert (ws.path / "README.md").is_file()
    assert _git(racine, "rev-parse", "maestro/socle-r1-init").strip() == _git(
        racine, "rev-parse", "main"
    ).strip()


@besoin_de_git
def test_une_branche_existante_se_reprend_telle_quelle_meme_quand_elle_reprend(
    tmp_path: Path,
) -> None:
    """`reprend` ne dit d'où partir qu'à la **création** : une branche rejouée garde la sienne."""
    projet = _projet_git(tmp_path)
    with espace_de_travail(projet, tache_id="socle") as ws:
        (ws.path / "socle.txt").write_text("socle\n", encoding="utf-8")
        _commite(ws.path, "socle")
    with espace_de_travail(projet, tache_id="socle-r1-init") as ws:
        (ws.path / "init.txt").write_text("init\n", encoding="utf-8")
        _commite(ws.path, "init, parti de la base")

    with espace_de_travail(projet, tache_id="socle-r1-init", reprend="socle") as ws:
        assert (ws.path / "init.txt").is_file()
        assert not (ws.path / "socle.txt").exists()


@besoin_de_git
def test_la_branche_part_de_la_branche_de_base_declaree(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path, branche="develop")
    racine = Path(projet.racine)
    assert projet.vcs is not None and projet.vcs.branche_base == "develop"
    # Une branche `main` divergente existe aussi : c'est bien `develop` qui doit servir de base.
    _git(racine, "branch", "main")
    with espace_de_travail(projet, tache_id="t-1"):
        pass
    fusion = _git(racine, "merge-base", "maestro/t-1", "develop").strip()
    assert fusion == _git(racine, "rev-parse", "develop").strip()


@besoin_de_git
def test_une_branche_de_base_disparue_ne_condamne_pas_la_tache(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    disparue = Projet(
        id=projet.id,
        nom=projet.nom,
        racine=projet.racine,
        vcs=Vcs(type="git", branche_base="jamais-creee"),
    )
    with espace_de_travail(disparue, tache_id="t-1") as ws:
        assert (ws.path / "README.md").is_file()  # parti de HEAD, sans broncher


@besoin_de_git
def test_keep_conserve_le_worktree(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1", keep=True) as ws:
        chemin = ws.path
    try:
        assert (chemin / "README.md").is_file()
        assert chemin.as_posix() in _git(racine, "worktree", "list").replace("\\", "/")
    finally:
        _git(racine, "worktree", "remove", "--force", str(chemin))
        shutil.rmtree(chemin.parent, ignore_errors=True)


@besoin_de_git
def test_un_enregistrement_orphelin_ne_bloque_pas_le_montage(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1", keep=True) as ws:
        orphelin = ws.path
    # Le répertoire disparaît sans que Git en soit informé (session tuée, disque nettoyé) :
    # sans `worktree prune`, Git refuserait la branche pour cause de « already checked out ».
    shutil.rmtree(orphelin.parent, ignore_errors=True)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        assert (ws.path / "README.md").is_file()
    assert orphelin.as_posix() not in _git(racine, "worktree", "list").replace("\\", "/")


@besoin_de_git
def test_un_worktree_abandonne_rend_sa_branche_avec_son_travail(tmp_path: Path) -> None:
    """La tâche en vol quand Maestro s'éteint : process tué, worktree resté, travail non commité.

    Le défaut de S12 (2026-10-05, #1391) : à la reprise du run sur son plan, la même
    tâche — même identifiant, même branche — se voyait refuser son worktree, « already
    used by worktree », et le run repris tombait en échec sur la tâche même qu'il
    devait refaire. Le `prune` n'y pouvait rien : le répertoire existe encore.

    Le travail laissé en plan est commité sur la branche avant que le worktree ne soit
    retiré — le retirer d'abord l'emporterait —, et la tâche remonte en le retrouvant.
    """
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1", keep=True) as ws:
        abandonne = ws.path
    # Ce qu'un process tué laisse : un worktree monté, du travail que personne n'a commité.
    (abandonne / "en_cours.txt").write_text("à moitié écrit\n", encoding="utf-8")

    with espace_de_travail(projet, tache_id="t-1") as ws:
        assert (ws.path / "en_cours.txt").read_text(encoding="utf-8") == "à moitié écrit\n"
    assert abandonne.as_posix() not in _git(racine, "worktree", "list").replace("\\", "/")


@besoin_de_git
def test_un_worktree_ouvert_par_la_personne_n_est_jamais_touche(tmp_path: Path) -> None:
    """Seule la forme d'un espace de Maestro se libère : jamais le worktree de quelqu'un.

    Une personne qui a ouvert elle-même un worktree sur `maestro/t-1` garde son
    travail et son worktree ; la tâche, elle, se voit refuser le sien — le refus de Git,
    dit, comme avant.
    """
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    a_soi = tmp_path / "le-mien" / "t-1"
    _git(racine, "worktree", "add", "--quiet", "-b", branche_de_tache("t-1"), str(a_soi))
    (a_soi / "brouillon.txt").write_text("à moi\n", encoding="utf-8")

    with pytest.raises(EspaceProjetIndisponible):
        with espace_de_travail(projet, tache_id="t-1"):
            pass

    assert (a_soi / "brouillon.txt").read_text(encoding="utf-8") == "à moi\n"
    assert a_soi.as_posix() in _git(racine, "worktree", "list").replace("\\", "/")


# --------------------------------------------------------------------------- #
# Le recensement du worktree est celui que Git voit (#1388)
# --------------------------------------------------------------------------- #


def _projet_git_qui_ignore(tmp_path: Path) -> Projet:
    """Un projet versionné qui ignore ses dépendances et sa sortie de build — p5, en petit."""
    racine = tmp_path / "projets" / "p5"
    (racine / "src").mkdir(parents=True)
    (racine / ".gitignore").write_text("node_modules/\n.next/\n", encoding="utf-8")
    (racine / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (racine / "package.json").write_text('{"name": "p5"}\n', encoding="utf-8")
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(racine, "add", "-A")
    _git(
        racine, "-c", "user.email=tests@maestro", "-c", "user.name=Tests",
        "commit", "--quiet", "-m", "socle",
    )
    return _projet(racine, vcs=detecter_vcs(racine))


@besoin_de_git
def test_le_recensement_du_worktree_ne_rend_pas_ce_que_git_ignore(tmp_path: Path) -> None:
    """Le défaut du run `da0a8ae6f1b2` : 22 891 fichiers de `node_modules/` au prompt du juge.

    L'empreinte du worktree est prise **avant** que l'agent n'installe quoi que ce
    soit : tout ce qu'un `npm install` ou un `next build` dépose ressortait donc en
    « fichier produit ». Git, lui, respecte le `.gitignore` — et c'est ce qu'il voit
    qui part sur la branche, donc ce que la tâche a produit.
    """
    projet = _projet_git_qui_ignore(tmp_path)
    with espace_de_travail(projet, tache_id="socle-nextjs") as ws:
        # Ce qu'un `npm install` puis un `next build` déposent : ignoré par le projet.
        (ws.path / "node_modules" / "react").mkdir(parents=True)
        (ws.path / "node_modules" / "react" / "index.js").write_text("x", encoding="utf-8")
        (ws.path / ".next" / "server").mkdir(parents=True)
        (ws.path / ".next" / "server" / "page.js").write_text("y", encoding="utf-8")
        # Ce que l'agent a écrit : un fichier neuf, un fichier suivi modifié.
        (ws.path / "app").mkdir()
        (ws.path / "app" / "page.tsx").write_text("export default null\n", encoding="utf-8")
        (ws.path / "src" / "app.py").write_text("print('bonjour')\n", encoding="utf-8")

        produits = [f.chemin for f in ws.produced_files()]

    assert produits == ["app/page.tsx", "src/app.py"]


@besoin_de_git
def test_le_recensement_du_worktree_est_celui_que_la_branche_porte(tmp_path: Path) -> None:
    """Le juge et la branche lisent le même ensemble : ce qui est commité au démontage."""
    projet = _projet_git_qui_ignore(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        (ws.path / "node_modules").mkdir()
        (ws.path / "node_modules" / "paquet.js").write_text("x", encoding="utf-8")
        (ws.path / "lib").mkdir()
        (ws.path / "lib" / "outil.ts").write_text("export {}\n", encoding="utf-8")
        produits = {f.chemin for f in ws.produced_files()}

    portes = set(
        _git(racine, "diff", "--name-only", "main...maestro/t-1").splitlines()
    )
    assert produits == portes == {"lib/outil.ts"}


# --------------------------------------------------------------------------- #
# L'agent sait ce que devient ce qu'il laisse dans sa copie de travail (#1401)
# --------------------------------------------------------------------------- #


@besoin_de_git
def test_la_copie_de_travail_dit_que_ce_qui_reste_est_commite(tmp_path: Path) -> None:
    """Le défaut de p5 : en copie de travail, rien ne disait à l'agent que ce qu'il
    laisse est commité au démontage (`git add -A`), brouillons compris, puis fusionné
    dans le projet — ni où mettre ses brouillons pour qu'ils ne le soient pas, puisque
    aucun atelier n'y est ouvert (#944). La consigne le dit, nomme la branche, et
    envoie les brouillons dans un dossier que la portée laisse à l'agent."""
    projet = _projet_git_qui_ignore(tmp_path)

    with espace_de_travail(projet, tache_id="socle-nextjs") as ws:
        consigne = " ".join(ws.consigne_espace().split())
        portee = portee_de(ws.path, projet)

    assert CONSIGNE_COPIE_DE_TRAVAIL in consigne
    assert "copie de travail" in consigne
    assert "est commité sur ta branche" in consigne
    assert "brouillons compris" in consigne
    # Ce qui n'est pas commité, c'est ce que le `.gitignore` du projet retire.
    assert "`.gitignore`" in consigne
    # Où vont les brouillons : un dossier créé par l'agent, hors de la copie.
    assert "`mktemp -d`" in consigne
    assert portee.commande_hors_portee('T=$(mktemp -d) && echo essai > "$T/notes.md"') == ""
    # Une branche neuve n'a pas de travail antérieur à annoncer (#1392).
    assert "n'est pas neuve" not in consigne


@besoin_de_git
def test_ce_que_la_copie_de_travail_annonce_est_ce_que_sa_branche_porte(tmp_path: Path) -> None:
    """La consigne n'est vraie que si le démontage fait ce qu'elle dit : un brouillon
    laissé dans la copie part sur la branche avec le livrable, et ce que le
    `.gitignore` du projet retire — dépendances, sortie de build — n'y part pas."""
    projet = _projet_git_qui_ignore(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1") as ws:
        (ws.path / "app").mkdir()
        (ws.path / "app" / "page.tsx").write_text("export default null\n", encoding="utf-8")
        (ws.path / "notes-de-travail.md").write_text("brouillon\n", encoding="utf-8")
        (ws.path / "node_modules" / "react").mkdir(parents=True)
        (ws.path / "node_modules" / "react" / "index.js").write_text("x", encoding="utf-8")
        (ws.path / ".next" / "cache").mkdir(parents=True)
        (ws.path / ".next" / "cache" / "build.json").write_text("{}", encoding="utf-8")

    portes = set(_git(racine, "diff", "--name-only", "main...maestro/t-1").splitlines())
    assert portes == {"app/page.tsx", "notes-de-travail.md"}


@besoin_de_git
def test_le_message_de_la_tache_porte_la_copie_de_travail(tmp_path: Path) -> None:
    """Le bout de chaîne : c'est dans le message de sa tâche que l'agent le lit."""
    projet = _projet_git_qui_ignore(tmp_path)
    fournisseur = _FournisseurEcrivain()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)

    asyncio.run(runtime.execute("Construire le socle", projet=projet, tache_id="t1"))

    (prompt,) = fournisseur.prompts
    assert CONSIGNE_COPIE_DE_TRAVAIL in prompt


def test_un_git_qui_refuse_l_enumeration_est_une_erreur_motivee(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Jamais de repli silencieux sur tout le disque : c'est ce repli qui faisait le défaut."""
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "paquet.js").write_text("x", encoding="utf-8")

    def _git_fache(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="", stderr="fatal: not a git repository\n"
        )

    monkeypatch.setattr(subprocess, "run", _git_fache)
    with pytest.raises(EspaceProjetIndisponible) as refus:
        EspaceCopieDeTravail(path=tmp_path).produced_files()
    assert refus.value.motif == "enumeration-refusee"
    assert "not a git repository" in str(refus.value)


def test_un_git_absent_a_l_enumeration_est_une_erreur_motivee(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _sans_git(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _sans_git)
    with pytest.raises(EspaceProjetIndisponible) as refus:
        EspaceCopieDeTravail.derive(tmp_path)
    assert refus.value.motif == "git-indisponible"


# --------------------------------------------------------------------------- #
# Refus motivés
# --------------------------------------------------------------------------- #


def test_git_absent_est_un_refus_motive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projet = _projet(tmp_path / "vide", vcs=Vcs(type="git", branche_base="main"))
    Path(projet.racine).mkdir(parents=True)

    def _sans_git(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _sans_git)
    with pytest.raises(EspaceProjetIndisponible) as refus:
        with espace_de_travail(projet, tache_id="t-1"):
            pass  # pragma: no cover - le montage a déjà échoué
    assert refus.value.motif == "git-indisponible"


def test_worktree_refuse_est_un_refus_motive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projet = _projet(tmp_path / "vide", vcs=Vcs(type="git", branche_base="main"))
    Path(projet.racine).mkdir(parents=True)

    def _git_fache(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="", stderr="fatal: invalid reference: HEAD\n"
        )

    monkeypatch.setattr(subprocess, "run", _git_fache)
    with pytest.raises(EspaceProjetIndisponible) as refus:
        with espace_de_travail(projet, tache_id="t-1"):
            pass  # pragma: no cover - le montage a déjà échoué
    assert refus.value.motif == "worktree-refuse"
    assert "invalid reference" in str(refus.value)


def test_un_temporaire_dans_la_racine_est_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    racine = Path(_projet_copie(tmp_path).racine)
    projet = _projet(racine, vcs=Vcs(type="git", branche_base="main"))
    # Un TMPDIR posé *dans* le projet ferait du worktree une écriture en place —
    # contrôle joué avant tout appel à Git, donc sans dépôt réel. Par la variable
    # et non par `tempfile.tempdir` depuis #992 : c'est l'environnement qui décide
    # de la racine des espaces (`maestro.sandbox.ramassage.racine_des_espaces`),
    # et c'est de là que vient le cas réel que ce contrôle attrape.
    monkeypatch.setenv("TMPDIR", projet.racine)
    with pytest.raises(EspaceProjetIndisponible) as refus:
        with espace_de_travail(projet, tache_id="t1"):
            pass  # pragma: no cover - le montage a déjà échoué
    assert refus.value.motif == "espace-dans-la-racine"


def test_une_racine_disparue_est_refusee_avec_son_motif(tmp_path: Path) -> None:
    projet = _projet(tmp_path / "projets" / "envole")
    with pytest.raises(RacineRefusee) as refus:
        with espace_de_travail(projet, tache_id="t1"):
            pass  # pragma: no cover - la validation a déjà échoué
    assert refus.value.motif == "dossier-absent"


# --------------------------------------------------------------------------- #
# Nommage de la branche
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("tache_id", "attendu"),
    [
        ("t1", "maestro/t1"),
        ("T-3 Analyse", "maestro/T-3-Analyse"),
        ("../evasion", "maestro/evasion"),
        ("a..b", "maestro/a-b"),
        ("refs.lock", "maestro/refs-lock"),
        ("", "maestro/tache"),
        ("@{}~^:?*[", "maestro/tache"),
    ],
)
def test_le_nom_de_branche_est_assaini(tache_id: str, attendu: str) -> None:
    assert branche_de_tache(tache_id) == attendu


# --------------------------------------------------------------------------- #
# Câblage : du `projet_id` de la tâche à l'espace de travail
# --------------------------------------------------------------------------- #


class _FournisseurEcrivain(ModelProvider):
    """Fournisseur factice outillé : note l'espace reçu et y écrit un fichier."""

    name = "ecrivain"

    def __init__(self) -> None:
        self.espaces: list[Path] = []
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None,
        on_arbitrage=None, on_blocage=None, on_decision=None, credit_arbitrage=None,
        on_courrier=None, on_question=None,
        on_processus=None,
        plafond_tours=None, projet=None,
    ):
        self.espaces.append(Path(workspace))
        self.prompts.append(prompt)
        (Path(workspace) / "RAPPORT.md").write_text("fait", encoding="utf-8")
        return "Fait."


def test_le_runtime_derive_l_espace_du_projet(tmp_path: Path) -> None:
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    fournisseur = _FournisseurEcrivain()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)

    issue = asyncio.run(runtime.execute("Corriger le calcul", projet=projet, tache_id="t1"))

    (espace,) = fournisseur.espaces
    assert espace.resolve() == racine.resolve()  # non versionné : en place (#839)
    assert (racine / "RAPPORT.md").read_text(encoding="utf-8") == "fait"  # et rien n'est retiré
    # L'agent a bien vu le projet, et son livrable ne contient que ce qu'il a écrit.
    assert [f.chemin for f in issue.fichiers] == ["RAPPORT.md"]


def test_l_executeur_relit_le_projet_de_la_tache(tmp_path: Path) -> None:
    depot = ProjetStore(tmp_path / "depot")
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    _arborescence(racine)
    projet = depot.creer("Démo", racine)

    def _tache(id_: str, projet_id: str | None) -> Task:
        return Task(
            id=id_,
            titre="Corriger le calcul",
            description="d",
            competences_requises=(),
            format_sortie="markdown",
            projet_id=projet_id,
        )

    executeur = LocalExecutor(_FournisseurEcrivain(), projets=depot)

    relu = executeur._projet(_tache("t1", projet.id))
    assert relu is not None and relu.racine == projet.racine
    assert executeur._projet(_tache("t2", None)) is None
    # Un `projet_id` orphelin ramène au `mkdtemp()` d'avant plutôt qu'à un échec.
    assert executeur._projet(_tache("t3", "prj-00000000")) is None
    # Sans dépôt câblé, aucune dérivation : le câblage est ce qui l'active.
    assert LocalExecutor(_FournisseurEcrivain())._projet(_tache("t4", projet.id)) is None


# --------------------------------------------------------------------------- #
# L'exécuteur : le journal dit ce qui est arrivé au projet, une tâche à la fois (#839)
# --------------------------------------------------------------------------- #


class _FournisseurMuet(ModelProvider):
    """Fournisseur factice qui réussit **sans rien écrire** : le cas de la racine vide."""

    name = "muet"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        return "Rien à écrire."


class _FournisseurQuiTombe(ModelProvider):
    """Fournisseur factice qui écrit puis **échoue** : ce qui est écrit doit rester."""

    name = "tombe"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        (Path(workspace) / "AVANT-LA-CHUTE.md").write_text("écrit puis tombé", encoding="utf-8")
        raise RuntimeError("aléa fournisseur")


class _FournisseurBarriere(ModelProvider):
    """Compte les exécutions **en vol** : c'est la mesure de la sérialisation.

    Chaque exécution signale son arrivée puis attend que `attendus` soient là,
    l'attente étant **bornée** : sur un projet non versionné, l'autre ne viendra
    pas tant que celle-ci tient l'atelier, et l'attente expire — c'est le verdict
    attendu, pas un aléa. Sans projet, les deux arrivent et la barrière se lève.
    Une barrière et non un `sleep` (règle de #292) : c'est elle qui rend la
    simultanéité **certaine** quand rien ne l'interdit.
    """

    name = "barriere"

    def __init__(self, attendus: int) -> None:
        self._attendus = attendus
        self._tous_la = asyncio.Event()
        self.arrivees = 0
        self.en_vol = 0
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        self.en_vol += 1
        self.pic = max(self.pic, self.en_vol)
        self.arrivees += 1
        if self.arrivees >= self._attendus:
            self._tous_la.set()
        try:
            await asyncio.wait_for(self._tous_la.wait(), timeout=0.5)
        except TimeoutError:
            pass  # sérialisé : l'autre attend l'atelier, elle ne viendra pas
        self.en_vol -= 1
        (Path(workspace) / "RAPPORT.md").write_text("fait", encoding="utf-8")
        return "Fait."


def _tache_routee(id_: str, projet_id: str | None) -> Task:
    """Une tâche que le routeur confie au développeur, seul rôle outillé monté ici."""
    return Task(
        id=id_,
        titre=f"Tâche {id_}",
        description="d",
        competences_requises=("backend",),
        format_sortie="markdown",
        projet_id=projet_id,
    )


def _executeur(fournisseur: ModelProvider, depot: ProjetStore | None) -> LocalExecutor:
    runtimes = {DEVELOPER_PROFILE.nom: AgentRuntime(fournisseur, DEVELOPER_PROFILE)}
    return LocalExecutor(fournisseur, runtimes=runtimes, projets=depot)


def _depot_et_projet(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet **non versionné** qui y est déclaré."""
    depot = ProjetStore(tmp_path / "depot")
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    _arborescence(racine)
    return depot, depot.creer("Démo", racine)


def _etape_projet(journal: RunJournal, tache_id: str):
    """L'unique ligne « Projet — … » de la tâche, ou l'assertion qui dit ce qu'il y a."""
    etapes = [r for r in journal.records if r.etape == f"{tache_id}:fusion"]
    assert len(etapes) == 1, [r.etape for r in journal.records]
    return etapes[0]


def _joue(fournisseur: ModelProvider, depot: ProjetStore | None, projet_id: str | None):
    journal = RunJournal(run_id="run-839")
    resultat = asyncio.run(
        _executeur(fournisseur, depot).execute(_tache_routee("t1", projet_id), [], journal)
    )
    return resultat, journal


class _FournisseurQuiRegarde(ModelProvider):
    """Fournisseur factice outillé : note si le travail du socle est sous ses pieds."""

    name = "regarde"

    def __init__(self) -> None:
        self.vus: list[bool] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **_):
        self.vus.append((Path(workspace) / "package.json").is_file())
        return "Fait."


@besoin_de_git
def test_l_executeur_monte_un_redecoupage_sur_la_branche_de_la_tache(tmp_path: Path) -> None:
    """De la tâche jusqu'au worktree (#1396) : `Task.reprend` atteint le montage."""
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer("Démo", Path(_projet_git(tmp_path).racine))
    assert projet.versionne
    with espace_de_travail(projet, tache_id="socle") as ws:
        (ws.path / "package.json").write_text('{"name": "p5"}\n', encoding="utf-8")
        _commite(ws.path, "le socle, livré")
    fournisseur = _FournisseurQuiRegarde()
    tache = replace(_tache_routee("socle-r1-init", projet.id), reprend="socle")

    resultat = asyncio.run(
        _executeur(fournisseur, depot).execute(tache, [], RunJournal(run_id="run-1396"))
    )

    assert resultat.ok, resultat.erreur
    assert fournisseur.vus == [True]


def test_l_ecriture_en_place_est_consignee_avec_ce_qui_a_ete_ecrit(tmp_path: Path) -> None:
    depot, projet = _depot_et_projet(tmp_path)
    resultat, journal = _joue(_FournisseurEcrivain(), depot, projet.id)
    assert resultat.ok
    assert (Path(projet.racine) / "RAPPORT.md").read_text(encoding="utf-8") == "fait"
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_ECRITURE_EN_PLACE
    assert projet.racine in etape.entree
    assert "1 fichier(s)" in etape.sortie and "RAPPORT.md" in etape.sortie


def test_une_racine_restee_vide_est_dite_et_non_tue(tmp_path: Path) -> None:
    """Le défaut de #568 : un run vert sur une racine vide. La ligne existe désormais."""
    depot, projet = _depot_et_projet(tmp_path)
    resultat, journal = _joue(_FournisseurMuet(), depot, projet.id)
    assert resultat.ok
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_ECRITURE_SANS_OBJET
    assert "rien n'a été écrit" in etape.sortie


def test_une_tache_en_echec_laisse_la_racine_telle_quelle_et_le_dit(tmp_path: Path) -> None:
    depot, projet = _depot_et_projet(tmp_path)
    resultat, journal = _joue(_FournisseurQuiTombe(), depot, projet.id)
    assert not resultat.ok
    assert (Path(projet.racine) / "AVANT-LA-CHUTE.md").is_file()  # rien n'est retiré
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_ECRITURE_EN_PLACE
    assert "tâche en échec" in etape.sortie


def test_un_projet_introuvable_est_nomme_au_journal(tmp_path: Path) -> None:
    depot = ProjetStore(tmp_path / "depot")
    resultat, journal = _joue(_FournisseurEcrivain(), depot, "prj-00000000")
    assert resultat.ok  # la tâche a travaillé dans un espace jetable (règle de `_projet`)
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_PROJET_INTROUVABLE
    assert "prj-00000000" in etape.sortie


def test_sans_projet_aucune_ligne_projet_n_est_consignee(tmp_path: Path) -> None:
    """La seule abstention muette : il n'y a pas de racine dont parler."""
    _, journal = _joue(_FournisseurEcrivain(), None, None)
    assert not any(r.etape.endswith(":fusion") for r in journal.records)


# --------------------------------------------------------------------------- #
# Une tâche que personne n'a prise ne raconte aucun travail d'agent (#1146)
# --------------------------------------------------------------------------- #


def _joue_sans_equipe(tmp_path: Path, depot: ProjetStore, projet_id: str):
    """La tâche d'un projet **qui n'a recruté personne** : le routeur la replie.

    Le dépôt d'agents est vide pour ce projet, donc `catalogue_du_projet` rend un
    tuple vide — « il n'y a personne », pas « je ne sais pas » (#1042) — et la
    tâche part en « à assigner » sans qu'aucun agent ne tourne. C'est le cas exact
    de l'essai du 2026-09-21 (`8a15f78f45d3`).
    """
    fournisseur = _FournisseurEcrivain()
    executeur = LocalExecutor(
        fournisseur,
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(fournisseur, DEVELOPER_PROFILE)},
        projets=depot,
        agents_store=AgentStore(tmp_path / "agents"),
    )
    journal = RunJournal(run_id="run-1146")
    resultat = asyncio.run(executeur.execute(_tache_routee("t1", projet_id), [], journal))
    return resultat, journal


def test_une_tache_a_assigner_ne_raconte_aucune_ecriture_d_agent(tmp_path: Path) -> None:
    """Critère 2 de #1146, sur un projet non versionné — le cas de l'essai réel.

    L'échantillon fautif est la phrase d'avant : « ce que l'agent a écrit avant
    d'échouer est resté dans … », écrite sur une tâche où aucun agent n'avait
    tourné. Le test lit la phrase **et** la racine : rien n'y a été écrit, et la
    ligne le dit au lieu de laisser chercher un travail qui n'existe pas.
    """
    depot, projet = _depot_et_projet(tmp_path)
    avant = sorted(p.name for p in Path(projet.racine).iterdir())

    resultat, journal = _joue_sans_equipe(tmp_path, depot, projet.id)

    assert not resultat.ok and resultat.role == ROLE_A_ASSIGNER
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_ECRITURE_SANS_OBJET
    assert "aucun agent n'a pris cette tâche" in etape.sortie
    assert "avant d'échouer" not in etape.sortie
    assert sorted(p.name for p in Path(projet.racine).iterdir()) == avant


@besoin_de_git
def test_une_tache_a_assigner_n_ouvre_ni_ne_fusionne_aucune_branche(tmp_path: Path) -> None:
    """Le même critère sur un projet versionné : « la branche conserve le travail » serait faux.

    La phrase d'avant renvoyait vers une branche de tâche qui n'avait jamais été
    créée, faute d'agent pour la monter. Le test vérifie l'absence de la branche
    dans le dépôt, pas seulement dans la phrase.
    """
    depot = ProjetStore(tmp_path / "depot")
    projet_git = _projet_git(tmp_path)
    projet = depot.creer("Versionné", Path(projet_git.racine))

    resultat, journal = _joue_sans_equipe(tmp_path, depot, projet.id)

    assert resultat.role == ROLE_A_ASSIGNER
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_FUSION_NON_TENTEE
    assert "aucune branche n'a été ouverte" in etape.sortie
    assert "conserve le travail" not in etape.sortie
    branches = _git(Path(projet.racine), "branch", "--list", branche_de_tache("t1"))
    assert branches.strip() == ""


def test_une_tache_a_assigner_d_un_projet_introuvable_ne_parle_d_aucun_espace(
    tmp_path: Path,
) -> None:
    """Troisième régime : « la tâche a travaillé dans un espace jetable » serait faux aussi."""
    depot = ProjetStore(tmp_path / "depot")

    resultat, journal = _joue_sans_equipe(tmp_path, depot, "prj-00000000")

    assert resultat.role == ROLE_A_ASSIGNER
    etape = _etape_projet(journal, "t1")
    assert etape.statut == STATUT_PROJET_INTROUVABLE
    assert "aucun agent n'a pris cette tâche" in etape.sortie
    assert "espace jetable" not in etape.sortie


def test_deux_taches_du_meme_projet_non_versionne_ne_travaillent_jamais_ensemble(
    tmp_path: Path,
) -> None:
    """Le régime de concurrence retenu : **sérialisation par projet** (`_atelier_projet`)."""
    depot, projet = _depot_et_projet(tmp_path)

    async def _deux(executeur: LocalExecutor, projet_id: str | None):
        journal = RunJournal(run_id="run-839")
        return await asyncio.gather(
            executeur.execute(_tache_routee("t1", projet_id), [], journal),
            executeur.execute(_tache_routee("t2", projet_id), [], journal),
        )

    # Le motif d'abord : sans projet, le même fournisseur voit bien deux exécutions
    # ensemble — sans quoi un pic de 1 ne prouverait rien.
    temoin = _FournisseurBarriere(attendus=2)
    asyncio.run(_deux(_executeur(temoin, depot), None))
    assert temoin.pic == 2

    # Puis la règle : dans la racine d'un projet non versionné, une seule à la fois.
    serialise = _FournisseurBarriere(attendus=2)
    resultats = asyncio.run(_deux(_executeur(serialise, depot), projet.id))
    assert all(r.ok for r in resultats)
    assert serialise.pic == 1


# --------------------------------------------------------------------------- #
# Ce que la vérification joue dans la copie n'atteint pas la branche (#1448)
# --------------------------------------------------------------------------- #

#: Ce que l'agent livre à chaque session — la seconde corrige la première.
_TRAVAIL_1448 = (
    {"tests/recettes.test.ts": "test('v1')\n", "src/app.py": "print('agent v1')\n"},
    {"tests/recettes.test.ts": "test('v2')\n", "src/app.py": "print('agent v2')\n"},
)


def _verifier_en_laissant_des_traces(espace: Path) -> None:
    """Ce qu'une vérification laisse en jouant ses contrôles — le run `3a7fa26ba927`, en petit.

    Un essai de Vitest avec le rapporteur `json` (`.vitest/json/output.json`, que
    `create-next-app` n'ignore pas), un formateur qui réécrit un fichier de l'agent,
    un `npm install` qui retouche le manifeste sans le changer et remplit les
    dépendances, que le projet ignore — tout ce que le prompt du vérificateur lui
    interdit, et qu'un essai fait quand même.
    """
    (espace / ".vitest" / "json").mkdir(parents=True, exist_ok=True)
    (espace / ".vitest" / "json" / "output.json").write_text(
        '{"numFailedTests": 1}', encoding="utf-8"
    )
    (espace / "src" / "app.py").write_text("print( 'formaté' )\n", encoding="utf-8")
    manifeste = espace / "package.json"
    etat = manifeste.stat()
    os.utime(manifeste, ns=(etat.st_atime_ns, etat.st_mtime_ns + 5_000_000_000))
    (espace / "node_modules" / "vitest").mkdir(parents=True, exist_ok=True)
    (espace / "node_modules" / "vitest" / "index.js").write_text("x", encoding="utf-8")


def _visible(espace: Path) -> dict[str, str]:
    """Ce que la branche porterait de `espace` à cet instant, chemin par chemin."""
    relatifs = _git(espace, "ls-files", "--cached", "--others", "--exclude-standard")
    return {
        relatif: (espace / relatif).read_text(encoding="utf-8")
        for relatif in relatifs.splitlines()
        if (espace / relatif).is_file()
    }


class _AgentQuiCorrige(ModelProvider):
    """Livre `_TRAVAIL_1448` session après session ; relève ce qu'il trouve et ce qu'il laisse."""

    name = "agent-qui-corrige"

    def __init__(self) -> None:
        self.trouve: list[dict[str, str]] = []
        self.laisse: list[dict[str, str]] = []
        self.dependances: list[bool] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, **_):
        espace = Path(workspace)
        self.trouve.append(_visible(espace))
        self.dependances.append((espace / "node_modules" / "vitest" / "index.js").is_file())
        for chemin, contenu in _TRAVAIL_1448[len(self.trouve) - 1].items():
            (espace / chemin).parent.mkdir(parents=True, exist_ok=True)
            (espace / chemin).write_text(contenu, encoding="utf-8")
        self.laisse.append(_visible(espace))
        return f"Livré (session {len(self.trouve)})."


async def _juge_qui_laisse_des_traces(
    espace: Path, sortie: str, fichiers, *, lieu: LieuSansTrace
) -> str | None:
    """La recette, réduite à son geste sur l'espace : la première livraison ne tient pas.

    Elle joue là où l'espace le lui dit — dans une copie de travail, la copie elle-même.
    """
    assert lieu.chemin == espace
    _verifier_en_laissant_des_traces(lieu.chemin)
    return None if "session 2" in sortie else "Ta livraison ne tient pas encore."


def _livre_et_verifie(tmp_path: Path) -> tuple[Path, _AgentQuiCorrige, object]:
    """Une tâche de tests sur un projet versionné, vérifiée deux fois, corrigée une fois."""
    projet = _projet_git_qui_ignore(tmp_path)
    agent = _AgentQuiCorrige()
    issue = asyncio.run(
        AgentRuntime(agent, DEVELOPER_PROFILE).execute(
            "Écrire les tests des données",
            projet=projet,
            tache_id="tests-donnees",
            on_livraison=_juge_qui_laisse_des_traces,
        )
    )
    return Path(projet.racine), agent, issue


@besoin_de_git
def test_ce_que_la_verification_laisse_n_atteint_pas_la_branche(tmp_path: Path) -> None:
    """Le défaut du run `3a7fa26ba927` : `.vitest/json/output.json` commité, puis fusionné.

    La branche d'une tâche porte le travail de l'agent, et rien de ce que Maestro a
    joué pour le juger : ni le rapport de Vitest, ni le fichier que le formateur a
    réécrit après la dernière livraison, ni le manifeste qu'un `npm install` a
    seulement retouché — qui n'entre pas davantage aux fichiers produits.
    """
    racine, _agent, issue = _livre_et_verifie(tmp_path)

    portes = _git(racine, "diff", "--name-only", "main...maestro/tests-donnees").split()
    assert sorted(portes) == ["src/app.py", "tests/recettes.test.ts"]
    assert _git(racine, "show", "maestro/tests-donnees:src/app.py") == "print('agent v2')\n"
    assert sorted(f.chemin for f in issue.fichiers) == ["src/app.py", "tests/recettes.test.ts"]


@besoin_de_git
def test_l_agent_qui_corrige_repart_de_son_espace_tel_qu_il_l_a_laisse(tmp_path: Path) -> None:
    """Le retour d'une livraison qui ne tient pas part de l'espace de l'agent, intact.

    Ce que la vérification a laissé n'y est plus, ce qu'elle a réécrit est revenu à
    la version de l'agent — et ce que le projet ignore, les dépendances qu'un
    contrôle a installées, est resté : ce n'est le livrable de personne.
    """
    _racine, agent, _issue = _livre_et_verifie(tmp_path)

    assert len(agent.trouve) == 2
    assert agent.trouve[1] == agent.laisse[0]
    assert ".vitest/json/output.json" not in agent.trouve[1]
    assert agent.dependances == [False, True]


@besoin_de_git
def test_une_verification_qui_leve_rend_quand_meme_la_copie(tmp_path: Path) -> None:
    """Une livraison non tenue lève depuis la vérification : la copie est rendue quand même.

    Et la cause garde sa route — c'est elle qui condamne la tâche.
    """
    projet = _projet_git_qui_ignore(tmp_path)
    with espace_de_travail(projet, tache_id="t-1448") as ws:
        (ws.path / "lib.ts").write_text("export {}\n", encoding="utf-8")
        avant = _visible(ws.path)
        with pytest.raises(RuntimeError, match="la cause de la tâche"):
            with ws.sans_trace():
                _verifier_en_laissant_des_traces(ws.path)
                raise RuntimeError("la cause de la tâche")
        assert _visible(ws.path) == avant
        assert not (ws.path / ".vitest").exists()


@besoin_de_git
def test_un_reste_qui_ne_se_defait_pas_se_nomme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un reste que la branche porterait se nomme, il ne se tait pas.

    L'index de l'instantané, lui, ne reste pas sous la racine jetable.
    """
    import maestro.sandbox.projet as module

    instantanes: list[Path] = []
    jetable_reel = module.jetable

    def jetable_releve(prefixe: str) -> Path:
        dossier = jetable_reel(prefixe)
        if prefixe == module.PREFIXE_INSTANTANE:
            instantanes.append(dossier)
        return dossier

    monkeypatch.setattr(module, "jetable", jetable_releve)
    monkeypatch.setattr(module, "_retirer", lambda racine, relatif: False)
    projet = _projet_git_qui_ignore(tmp_path)
    with espace_de_travail(projet, tache_id="t-1448") as ws:
        with pytest.raises(EspaceProjetIndisponible, match=r"\.vitest/json/output\.json"):
            with ws.sans_trace():
                _verifier_en_laissant_des_traces(ws.path)

    assert len(instantanes) == 1 and not instantanes[0].exists()


def test_la_racine_d_un_projet_non_versionne_n_est_jamais_defaite(tmp_path: Path) -> None:
    """En place, la racine est le dossier vivant de la personne : rien n'y est effacé après coup."""
    projet = _projet_copie(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t-1448") as ws:
        with ws.sans_trace():
            (racine / "ecrit-pendant.txt").write_text("par la personne", encoding="utf-8")

    assert (racine / "ecrit-pendant.txt").read_text(encoding="utf-8") == "par la personne"


# --------------------------------------------------------------------------- #
# La livraison se confronte à ce que la base a reçu, l'espace encore ouvert (#1548)
# --------------------------------------------------------------------------- #

_IDENTITE = ("-c", "user.email=tests@maestro", "-c", "user.name=Tests")


def _la_base_avance(racine: Path, contenu: str) -> None:
    """La base reçoit `src/app.py` = `contenu`, commité — une tâche voisine qui fusionne."""
    (racine / "src" / "app.py").write_text(contenu, encoding="utf-8")
    _git(racine, "add", "-A")
    _git(racine, *_IDENTITE, "commit", "--quiet", "-m", "tache voisine")


@besoin_de_git
def test_une_livraison_qui_fusionnerait_ne_porte_aucun_conflit(tmp_path: Path) -> None:
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t1") as ws:
        (ws.path / "nouveau.md").write_text("de l'agent\n", encoding="utf-8")
        _la_base_avance(racine, "print('voisin')\n")  # ailleurs que ce que l'agent touche

        assert conflit_avec_la_base(ws.path, projet, "t1") is None
        # La racine n'est pas la copie d'une tâche : il n'y a rien à y confronter.
        assert conflit_avec_la_base(racine, projet, "t1") is None


@besoin_de_git
def test_le_travail_non_commite_se_confronte_a_la_base_sans_que_rien_ne_bouge(
    tmp_path: Path,
) -> None:
    """Ce que le démontage commiterait compte, et le constat n'écrit nulle part."""
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t1") as ws:
        (ws.path / "src" / "app.py").write_text("print('agent')\n", encoding="utf-8")
        _la_base_avance(racine, "print('voisin')\n")
        avant = (
            _git(ws.path, "status", "--porcelain"),
            _git(ws.path, "rev-parse", "HEAD"),
            _git(racine, "rev-parse", "main"),
        )

        conflit = conflit_avec_la_base(ws.path, projet, "t1")

        assert conflit is not None
        assert conflit.fichiers == ("src/app.py",)
        assert (conflit.branche, conflit.base) == (branche_de_tache("t1"), "main")
        assert (conflit.commits, conflit.nb_commits) == (("tache voisine",), 1)
        assert not conflit.en_cours
        assert "git merge main" in conflit.consigne() and "src/app.py" in conflit.consigne()
        assert avant == (
            _git(ws.path, "status", "--porcelain"),
            _git(ws.path, "rev-parse", "HEAD"),
            _git(racine, "rev-parse", "main"),
        )


@besoin_de_git
def test_une_integration_laissee_en_conflit_est_rendue_et_resolue_elle_passe(
    tmp_path: Path,
) -> None:
    """L'agent a commencé d'intégrer la base : en conflit il le reste, résolue elle entre."""
    projet = _projet_git(tmp_path)
    racine = Path(projet.racine)
    with espace_de_travail(projet, tache_id="t1") as ws:
        (ws.path / "src" / "app.py").write_text("print('agent')\n", encoding="utf-8")
        _git(ws.path, "add", "-A")
        _git(ws.path, *_IDENTITE, "commit", "--quiet", "-m", "agent")
        _la_base_avance(racine, "print('voisin')\n")
        fusion = subprocess.run(
            [GIT or "git", *_IDENTITE, "merge", "main"],
            cwd=ws.path,
            capture_output=True,
            check=False,
        )
        assert fusion.returncode == 1

        laissee = conflit_avec_la_base(ws.path, projet, "t1")
        assert laissee is not None and laissee.en_cours
        assert laissee.fichiers == ("src/app.py",)

        # Résolue et indexée, la fusion pas encore commitée : le démontage la commitera
        # avec la base pour second parent, et c'est ce qui est confronté.
        (ws.path / "src" / "app.py").write_text(
            "print('voisin')\nprint('agent')\n", encoding="utf-8"
        )
        _git(ws.path, "add", "-A")
        assert conflit_avec_la_base(ws.path, projet, "t1") is None


def test_une_correction_qui_ne_reduit_pas_le_conflit_n_est_pas_un_progres() -> None:
    avant = ConflitAvecLaBase(branche="maestro/t1", base="main", tete="a", fichiers=("x", "y"))

    assert replace(avant, fichiers=("x",)).reduit(avant)
    assert not replace(avant).reduit(avant)
    assert not replace(avant, fichiers=("x", "z")).reduit(avant)
    # La base a bougé : c'est un autre conflit, pas l'échec de la correction.
    assert replace(avant, tete="b").reduit(avant)
    # L'intégration est commencée : un geste, pas une correction vaine.
    assert replace(avant, en_cours=True).reduit(avant)
