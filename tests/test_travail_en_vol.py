"""Le travail d'une tâche coupée en vol est sauvé, et sa reprise repart de lui (#1392).

Troisième lot de #1389. Sur p5, la tâche `socle-projet` tournait encore quand la
personne a mis le run en pause ; sept secondes plus tard l'extinction a demandé
l'annulation, attendu `DELAI_ANNULATION_S`, puis achevé le groupe de process de
l'hôte. Ce que la tâche avait écrit ne vivait alors **que** dans son worktree du
répertoire temporaire : le commit de sortie (#705) est dans le `finally` de
`espace_de_travail`, et un process tué ne déroule pas son `finally`. Et la reprise
du run sur son plan (#1391), qui rejoue la tâche sous le même identifiant, remontait
bien sa branche — mais l'agent n'en savait rien : il prenait ce qu'il trouvait pour
le projet, ou le refaisait.

Ce que ce fichier garde, et qui ne se voit nulle part ailleurs :

① **À l'interruption, le travail en vol est porté sur sa branche** — par celui qui a
   éteint le process, une fois qu'il l'a fait (`sauver_le_travail_en_vol`, appelé par
   `ServiceExecutions` après l'extinction de l'hôte ou le constat de sa mort), et sur
   les seuls worktrees de la forme d'un espace de Maestro : jamais celui d'une
   personne. Un commit que le projet refuse se dit, il n'arrête pas l'extinction.

② **À la reprise, la tâche sait ce que sa branche porte** (`TravailAnterieur`) : le
   message de sa tâche le lui dit — ce qui est fait, et qu'elle repart de là —, et ce
   travail compte dans ce qu'elle livre au juge, qu'elle l'ait réécrit ou non. Une
   branche neuve, ou déjà fusionnée, n'annonce aucun travail antérieur : le message ne
   porte que ce que toute copie de travail dit d'elle-même (où l'agent est, #1399, et
   que ce qu'il y laisse se commite, #1401).

③ **Un démontage coupé ne laisse pas de coquille** (#1447) : l'extinction tombait
   aussi pendant que l'hôte effaçait un `node_modules/` complet, et laissait à
   l'adresse du worktree un dossier sans `.git` que Git tenait encore pour celui de
   la branche — S12 s'y arrêtait. Le worktree est désormais intact ou retiré à tout
   instant, et une coquille d'avant le correctif n'arrête ni l'oracle de S12, ni la
   sauvegarde, ni le montage suivant, ni le ramassage. La mort de l'hôte est jouée
   par une exception que rien n'attrape, au milieu de l'effacement.

Aucun réseau, aucun modèle : les dépôts sont jetables, l'hôte est un double — ce qu'on
éprouve est *ce qui est sauvé, et ce qui est dit*, pas l'extinction d'un groupe de
process (`tests/test_hote_detache.py`). L'épreuve sur le réel est S12
(`maestro.scenarios`, docs/28 §12.10).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    RegistreBattementsMemoire,
    create_app,
)
from maestro.controltower.events import EVENEMENT_TACHE_STATUT
from maestro.controltower.executions import ServiceExecutions
from maestro.controltower.hote import HoteMort, HoteRun, OrdreRun
from maestro.controltower.progression import STATUT_INTERROMPUE
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.state import EXECUTION_EN_COURS
from maestro.engine import STATUT_EN_COURS
from maestro.projets.application import ApplicationRefusee
from maestro.projets.modele import Projet
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import (
    branche_de_tache,
    espace_de_travail,
    ramassage,
    sauver_le_travail_en_vol,
)
from maestro.sandbox import projet as espace_projet
from maestro.scenarios.projets import en_cours_d_ecriture

GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="git introuvable")

#: La tâche coupée en vol des scénarios — celle de S12, en petit.
TACHE = "donnees-recettes"


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` : sans lui, `valider_racine` refuse toute
    racine prise sous `AppData`, où vit le `tmp_path` de pytest sous Windows."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


# --------------------------------------------------------------------------- #
# Le décor : un projet versionné, et ce qu'un process tué laisse derrière lui
# --------------------------------------------------------------------------- #


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


def _projet(tmp_path: Path, depot: ProjetStore | None = None) -> Projet:
    """Un projet **versionné**, déclaré comme en vrai : un commit sur `main`."""
    racine = tmp_path / "projets" / "recettes"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "index.ts").write_text("export {}\n", encoding="utf-8")
    (racine / "README.md").write_text("# Recettes\n", encoding="utf-8")
    (racine / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _commite(racine, "socle")
    depot = depot if depot is not None else ProjetStore(tmp_path / "depot")
    return depot.creer("Recettes", racine)


@contextmanager
def _tache_tuee(projet: Projet, tache_id: str = TACHE) -> Iterator[Path]:
    """Le worktree d'une tâche dont le process a été tué en vol — rendu, puis retiré.

    `keep=True` est exactement ce qu'un process tué laisse : le worktree monté, son
    enregistrement chez Git, et rien de commité de ce qui s'y écrit ensuite. Le
    ménage final retire ce qui resterait, pour ne rien laisser au répertoire
    temporaire du poste.
    """
    with espace_de_travail(projet, tache_id=tache_id, keep=True) as ws:
        chemin = ws.path
    try:
        yield chemin
    finally:
        subprocess.run(
            [GIT or "git", "worktree", "remove", "--force", str(chemin)],
            cwd=projet.racine,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(chemin.parent, ignore_errors=True)


def _en_attente(espace: Path) -> str:
    """Ce que le worktree porte encore de non commité (`git status --porcelain`)."""
    return _git(espace, "status", "--porcelain").strip()


def _fichiers_de_la_branche(racine: Path, tache_id: str = TACHE) -> set[str]:
    """Ce que la branche de la tâche change par rapport à `main`."""
    sortie = _git(racine, "diff", "--name-only", f"main...{branche_de_tache(tache_id)}")
    return set(sortie.split())


# --------------------------------------------------------------------------- #
# ① À l'interruption, le travail en vol est porté sur sa branche
# --------------------------------------------------------------------------- #


def test_le_travail_d_une_tache_tuee_en_vol_est_porte_sur_sa_branche(tmp_path: Path) -> None:
    """Le défaut de p5 : process tué, `finally` jamais déroulé, travail hors branche."""
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    with _tache_tuee(projet) as tuee:
        (tuee / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")
        assert _fichiers_de_la_branche(racine) == set()  # rien de sauvé — le défaut

        assert sauver_le_travail_en_vol(projet, TACHE) is True

        assert _fichiers_de_la_branche(racine) == {"src/recettes.ts"}
        # Le worktree reste monté, et ne porte plus rien en attente : c'est le
        # montage suivant de la tâche qui le libère.
        assert tuee.is_dir()
        assert _en_attente(tuee) == ""


def test_ce_que_le_projet_ignore_n_entre_pas_sur_la_branche(tmp_path: Path) -> None:
    """Le geste est celui du démontage, `.gitignore` compris : pas de `node_modules/`."""
    projet = _projet(tmp_path)
    with _tache_tuee(projet) as tuee:
        (tuee / "node_modules" / "next").mkdir(parents=True)
        (tuee / "node_modules" / "next" / "index.js").write_text("x", "utf-8")
        (tuee / "package.json").write_text('{"name": "recettes"}\n', "utf-8")

        sauver_le_travail_en_vol(projet, TACHE)

        assert _fichiers_de_la_branche(Path(projet.racine)) == {"package.json"}


def test_un_demontage_qui_a_eu_lieu_ne_laisse_rien_a_porter(tmp_path: Path) -> None:
    """Le `finally` a eu le temps de tourner : plus de worktree, rien à faire ici."""
    projet = _projet(tmp_path)
    with espace_de_travail(projet, tache_id=TACHE) as ws:
        (ws.path / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")

    assert sauver_le_travail_en_vol(projet, TACHE) is False
    assert _fichiers_de_la_branche(Path(projet.racine)) == {"src/recettes.ts"}


def test_le_worktree_d_une_personne_n_est_jamais_commite(tmp_path: Path) -> None:
    """Seule la forme d'un espace de Maestro est sauvée : jamais le brouillon de quelqu'un."""
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    a_soi = tmp_path / "le-mien" / TACHE
    _git(racine, "worktree", "add", "--quiet", "-b", branche_de_tache(TACHE), str(a_soi))
    (a_soi / "brouillon.txt").write_text("à moi\n", encoding="utf-8")

    assert sauver_le_travail_en_vol(projet, TACHE) is False

    assert _en_attente(a_soi) == "?? brouillon.txt"
    assert _fichiers_de_la_branche(racine) == set()


def test_un_projet_non_versionne_n_a_rien_hors_de_sa_racine(tmp_path: Path) -> None:
    """En place (#839), l'agent écrit dans le projet : il n'y a rien à porter nulle part."""
    racine = tmp_path / "projets" / "brouillon"
    racine.mkdir(parents=True)
    projet = ProjetStore(tmp_path / "depot").creer("Brouillon", racine)
    assert not projet.versionne

    assert sauver_le_travail_en_vol(projet, TACHE) is False


def test_un_commit_que_le_projet_refuse_se_dit(tmp_path: Path) -> None:
    """Le `pre-commit` du projet a le dernier mot, et son refus remonte motivé.

    Rien n'est forcé (les hooks de l'utilisateur ne sont pas contournés) ; c'est
    l'appelant qui dit le refus et poursuit l'extinction.
    """
    projet = _projet(tmp_path)
    hook = Path(projet.racine) / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'pas de commit ici' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    with _tache_tuee(projet) as tuee:
        (tuee / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")

        with pytest.raises(ApplicationRefusee):
            sauver_le_travail_en_vol(projet, TACHE)
        assert _en_attente(tuee) != ""  # rien n'est perdu : le worktree le garde


# --------------------------------------------------------------------------- #
# ② À la reprise, la tâche sait ce que sa branche porte
# --------------------------------------------------------------------------- #


def _interrompue(projet: Projet) -> None:
    """Une tâche coupée en vol, son travail porté sur sa branche à l'extinction."""
    with _tache_tuee(projet) as tuee:
        (tuee / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")
        (tuee / "README.md").write_text("# Recettes\n\nTrois recettes.\n", "utf-8")
        sauver_le_travail_en_vol(projet, TACHE)


def test_la_tache_reprise_apprend_ce_que_sa_branche_porte(tmp_path: Path) -> None:
    """Le message de sa tâche dit ce qui est fait, et qu'elle repart de là."""
    projet = _projet(tmp_path)
    _interrompue(projet)

    with espace_de_travail(projet, tache_id=TACHE) as ws:
        consigne = ws.consigne_espace()
        assert (ws.path / "src" / "recettes.ts").is_file()  # le travail est sous ses pieds

    assert f"`{branche_de_tache(TACHE)}` n'est pas neuve" in consigne
    assert "que `main` n'a pas" in consigne
    assert "- ajouté : `src/recettes.ts`" in consigne
    assert "- modifié : `README.md`" in consigne
    assert "ne le refais pas depuis zéro" in consigne
    # Où elle est d'abord (#1399), ce que sa branche porte ensuite.
    assert consigne.index("racine du projet") < consigne.index("n'est pas neuve")


def test_ce_que_la_tache_avait_fait_compte_dans_sa_livraison(tmp_path: Path) -> None:
    """Une tâche reprise qui ne réécrit rien de ce qui est fait ne livre pas une moitié.

    Le juge lit les fichiers produits (#1388) : relevés au montage, ceux d'une
    tentative précédente ressortaient « inchangés », donc absents de la livraison.
    """
    projet = _projet(tmp_path)
    _interrompue(projet)

    with espace_de_travail(projet, tache_id=TACHE) as ws:
        (ws.path / "src" / "page.tsx").write_text("export default null\n", "utf-8")
        produits = {f.chemin for f in ws.produced_files()}

    assert produits == {"README.md", "src/page.tsx", "src/recettes.ts"}


#: Ce que la consigne d'une branche reprise dit, et qu'une branche neuve ne dit pas : la
#: copie de travail, elle, se présente toujours (#1399, #1401).
REPRISE = "n'est pas neuve"


def test_une_branche_neuve_ne_dit_rien_de_travail_anterieur(tmp_path: Path) -> None:
    """Le cas courant : rien à reprendre, rien d'annoncé — la copie de travail se
    présente (où l'agent est, #1399 ; ce qui se commite, #1401), sans travail antérieur."""
    projet = _projet(tmp_path)
    with espace_de_travail(projet, tache_id=TACHE) as ws:
        consigne = ws.consigne_espace()
    assert REPRISE not in consigne
    assert "- ajouté : " not in consigne
    assert "racine du projet" in consigne


def test_une_branche_deja_fusionnee_ne_dit_rien_de_travail_anterieur(tmp_path: Path) -> None:
    """Un travail que `main` a déjà n'est pas à reprendre : la branche ne porte rien de plus."""
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    _interrompue(projet)
    _git(racine, "merge", "--quiet", "--ff-only", branche_de_tache(TACHE))

    with espace_de_travail(projet, tache_id=TACHE) as ws:
        assert REPRISE not in ws.consigne_espace()
        assert ws.produced_files() == ()


def test_un_redecoupage_ne_prend_pas_le_travail_de_la_tache_reprise_pour_le_sien(
    tmp_path: Path,
) -> None:
    """La branche née de celle d'une autre tâche (#1396) se compare à elle, pas à la base."""
    projet = _projet(tmp_path)
    with espace_de_travail(projet, tache_id="socle") as ws:
        (ws.path / "package.json").write_text('{"name": "recettes"}\n', "utf-8")
        _commite(ws.path, "le socle")

    # Premier montage : la branche naît de celle du socle — elle n'a encore rien fait.
    with espace_de_travail(projet, tache_id="socle-r1", reprend="socle") as ws:
        assert REPRISE not in ws.consigne_espace()
        (ws.path / "src" / "init.ts").write_text("export {}\n", "utf-8")

    # Remontée : ce qu'elle a fait est le sien, le socle n'en est pas.
    with espace_de_travail(projet, tache_id="socle-r1", reprend="socle") as ws:
        consigne = ws.consigne_espace()
        produits = {f.chemin for f in ws.produced_files()}

    assert "que `maestro/socle` n'a pas" in consigne
    assert "`src/init.ts`" in consigne
    assert "package.json" not in consigne
    assert produits == {"src/init.ts"}


def test_ce_qui_est_dit_est_borne_et_le_reste_compte(tmp_path: Path) -> None:
    """Une borne de taille de prompt, jamais un silence : ce qui n'est pas nommé est compté."""
    projet = _projet(tmp_path)
    with _tache_tuee(projet) as tuee:
        for n in range(45):
            (tuee / "src" / f"f{n:02}.ts").write_text(f"export const n = {n}\n", "utf-8")
        sauver_le_travail_en_vol(projet, TACHE)

    with espace_de_travail(projet, tache_id=TACHE) as ws:
        consigne = ws.consigne_espace()

    assert "45 fichier(s)" in consigne
    assert consigne.count("- ajouté : ") == 40
    assert "- et 5 autre(s)" in consigne


class _FournisseurQuiLit(ModelProvider):
    """Fournisseur factice outillé : retient le message reçu, n'écrit rien."""

    name = "lecteur"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt: str, **_options: Any) -> str:
        self.prompts.append(prompt)
        return "Fait."


def test_le_message_de_la_tache_reprise_dit_ce_qui_est_deja_fait(tmp_path: Path) -> None:
    """Le deuxième critère, de bout en bout : c'est le **message de sa tâche** qui le dit."""
    projet = _projet(tmp_path)
    _interrompue(projet)
    fournisseur = _FournisseurQuiLit()
    runtime = AgentRuntime(fournisseur, DEVELOPER_PROFILE)

    issue = asyncio.run(runtime.execute("Écrire les données", projet=projet, tache_id=TACHE))

    (prompt,) = fournisseur.prompts
    assert "n'est pas neuve" in prompt
    assert "`src/recettes.ts`" in prompt
    assert {f.chemin for f in issue.fichiers} == {"README.md", "src/recettes.ts"}


# --------------------------------------------------------------------------- #
# ① (suite) Celui qui éteint l'hôte porte le travail : l'API
# --------------------------------------------------------------------------- #


class HoteDouble(HoteRun):
    """Un hôte qui ne lance aucun process — et ne déroule donc aucun `finally`.

    C'est précisément le cas qu'on éprouve : ce que l'hôte aurait dû sauver en
    sortant ne l'a pas été, et seul celui qui l'éteint peut encore le faire.
    """

    def __init__(self, *, morts: tuple[HoteMort, ...] = ()) -> None:
        self.lances: list[str] = []
        self._morts = morts

    async def lancer(self, ordre: OrdreRun) -> None:
        self.lances.append(ordre.run_id)

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        if run_id not in self.lances:
            return False
        self.lances.remove(run_id)
        return True

    def en_vol(self, run_id: str) -> bool:
        return run_id in self.lances

    def runs_en_vol(self) -> tuple[str, ...]:
        return tuple(self.lances)

    def ramasser(self) -> tuple[HoteMort, ...]:
        morts, self._morts = self._morts, ()
        return morts

    async def fermer(self, *, delai_s: float) -> None:
        return None


def _en_vol(state: ControlTowerState, run_id: str, projet: Projet) -> None:
    """La tâche du run est en cours dans le projet — l'état qu'on éteint."""
    state.appliquer(
        Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id=run_id,
            tache_id=TACHE,
            titre="Écrire les données",
            agent="dev",
            role="Développeur",
            statut=STATUT_EN_COURS,
            projet_id=projet.id,
        )
    )


def _app(tmp_path: Path, state: ControlTowerState, depot: ProjetStore) -> TestClient:
    """L'app réelle sur bus et journal mémoire — l'hôte est double, le dépôt est jetable."""
    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=state,
            event_log=InMemoryEventLog(),
            battements=RegistreBattementsMemoire(),
            hote_run=HoteDouble(),
            projets=ServiceProjets(depot, reglages=ReglagesProjetsStore(tmp_path / "reglages")),
        )
    )


def test_l_extinction_porte_le_travail_de_la_tache_en_vol_sur_sa_branche(
    tmp_path: Path,
) -> None:
    """Le premier critère : sauvé **à l'interruption**, pas seulement si le `finally` a tourné."""
    depot = ProjetStore(tmp_path / "depot")
    projet = _projet(tmp_path, depot)
    state = ControlTowerState()
    with _tache_tuee(projet) as tuee, _app(tmp_path, state, depot) as client:
        reponse = client.post("/api/executions", json={"objectif": "Un livre de recettes"})
        assert reponse.status_code == 202, reponse.text
        run_id = str(reponse.json()["run_id"])
        _en_vol(state, run_id, projet)
        (tuee / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")

        assert client.post("/api/extinction").status_code == 200

        assert _fichiers_de_la_branche(Path(projet.racine)) == {"src/recettes.ts"}
        assert _en_attente(tuee) == ""


def test_un_commit_refuse_n_empeche_pas_maestro_de_s_eteindre(tmp_path: Path) -> None:
    """Une sauvegarde refusée se dit ; le run est soldé quand même, sa tâche interrompue."""
    depot = ProjetStore(tmp_path / "depot")
    projet = _projet(tmp_path, depot)
    hook = Path(projet.racine) / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    state = ControlTowerState()
    with _tache_tuee(projet) as tuee, _app(tmp_path, state, depot) as client:
        run_id = str(client.post("/api/executions", json={"objectif": "Recettes"}).json()["run_id"])
        _en_vol(state, run_id, projet)
        (tuee / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")

        reponse = client.post("/api/extinction")

        assert reponse.status_code == 200
        assert [r["run_id"] for r in reponse.json()["runs"]] == [run_id]
        assert _en_attente(tuee) != ""  # rien n'est perdu : le worktree le garde
        # Le soldage des tâches est allé au bout : il se pousse par la pompe (#466).
        limite = time.monotonic() + 5.0
        while (tache := state.tache(TACHE)) is None or tache.statut != STATUT_INTERROMPUE:
            assert time.monotonic() < limite, "la tâche en vol n'a jamais été soldée"
            time.sleep(0.02)


def test_un_hote_mort_seul_laisse_son_travail_sur_sa_branche(tmp_path: Path) -> None:
    """L'autre chemin sans `finally` : l'hôte est tombé, personne ne l'a éteint."""
    depot = ProjetStore(tmp_path / "depot")
    projet = _projet(tmp_path, depot)
    state = ControlTowerState()
    run_id = "a1b2c3d4e5f6"
    hote = HoteDouble(morts=(HoteMort(run_id=run_id, cause="code 1 : plus rien"),))
    pilote = ServiceExecutions(InMemoryEventBus(), state, hote=hote, projets=depot)
    pilote._consigne(run_id, EXECUTION_EN_COURS, "Recettes", "lancée depuis la Control Tower")
    _en_vol(state, run_id, projet)
    with _tache_tuee(projet) as tuee:
        (tuee / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")

        asyncio.run(pilote._ramasser())

        assert _fichiers_de_la_branche(Path(projet.racine)) == {"src/recettes.ts"}


def test_sans_depot_de_projets_rien_n_est_porte(tmp_path: Path) -> None:
    """Le défaut des tests du service : rien n'est porté, et rien ne casse."""
    depot = ProjetStore(tmp_path / "depot")
    projet = _projet(tmp_path, depot)
    state = ControlTowerState()
    run_id = "f6e5d4c3b2a1"
    hote = HoteDouble(morts=(HoteMort(run_id=run_id, cause="code 1"),))
    pilote = ServiceExecutions(InMemoryEventBus(), state, hote=hote)
    pilote._consigne(run_id, EXECUTION_EN_COURS, "Recettes", "lancée depuis la Control Tower")
    _en_vol(state, run_id, projet)
    with _tache_tuee(projet) as tuee:
        (tuee / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")

        asyncio.run(pilote._ramasser())

        assert _fichiers_de_la_branche(Path(projet.racine)) == set()
        assert _en_attente(tuee) == "?? src/recettes.ts"


# --------------------------------------------------------------------------- #
# ③ Un démontage interrompu ne laisse pas de coquille (#1447)
# --------------------------------------------------------------------------- #


class _Tue(BaseException):
    """L'hôte achevé au milieu d'un geste : plus rien ne tourne après, `finally` compris.

    `BaseException` et non `Exception` : aucun repli du démontage ne l'attrape, comme
    aucun ne survit à un `taskkill /T /F`.
    """


#: Ce qu'efface le démontage quand l'hôte meurt : le défaut de #1447 tombait au
#: milieu de `node_modules/`, qu'un `npm install` complet rend long à effacer.
BUTEE = "node_modules"


def _effacer_jusqu_a_la_butee(dossier: Path) -> None:
    """Efface `dossier` dans l'ordre où NTFS le rend, et meurt en atteignant `BUTEE`.

    L'ordre est celui des deux passages de #1447 : tout ce qui précède
    `node_modules` dans l'alphabet était parti — `.git` le premier —, tout ce qui le
    suit était resté.
    """
    for entree in sorted(dossier.iterdir(), key=lambda chemin: chemin.name.casefold()):
        if entree.name == BUTEE:
            next(entree.rglob("*.js")).unlink()  # quelques fichiers, puis la mort
            raise _Tue
        if entree.is_dir():
            shutil.rmtree(entree)
        else:
            entree.unlink()


@pytest.fixture
def _temporaire_isole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le répertoire temporaire du poste sous `tmp_path` : ce qu'une mort laisse y reste."""
    temporaire = tmp_path / "temp"
    temporaire.mkdir()
    monkeypatch.setenv("TMPDIR", str(temporaire))
    return temporaire


@pytest.fixture
def _mort_pendant_l_effacement(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """L'hôte meurt pendant qu'un démontage efface, **quelle que soit** la façon d'effacer.

    Le démontage de #1447 effaçait par `git worktree remove --force`, celui d'après
    efface en Python : les deux gestes sont rejoués par `_effacer_jusqu_a_la_butee`,
    une fois armés — le montage, lui, se joue sans mort. Rend l'interrupteur.
    """
    arme: list[bool] = []
    git, retirer = espace_projet._git, espace_projet.retirer_arbre

    def _git(racine: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        if arme and arguments[:2] == ("worktree", "remove"):
            _effacer_jusqu_a_la_butee(Path(arguments[-1]))
        return git(racine, *arguments)

    def _retirer(chemin: Path) -> bool:
        if arme and chemin.is_dir() and (chemin / BUTEE).is_dir():
            _effacer_jusqu_a_la_butee(chemin)
        return retirer(chemin)

    monkeypatch.setattr(espace_projet, "_git", _git)
    monkeypatch.setattr(espace_projet, "retirer_arbre", _retirer)
    return arme


def _ecrit_avec_ses_dependances(espace: Path) -> None:
    """Une tâche de S12 en vol : son travail, et un `npm install` complet que le projet ignore."""
    (espace / "src" / "recettes.ts").write_text("export const recettes = []\n", "utf-8")
    (espace / "package.json").write_text('{"name": "recettes"}\n', "utf-8")
    (espace / BUTEE / "next").mkdir(parents=True)
    for n in range(3):
        (espace / BUTEE / "next" / f"m{n}.js").write_text("x", "utf-8")
    (espace / "tsconfig.json").write_text("{}\n", "utf-8")


def _worktrees_de_la_branche(racine: Path, tache_id: str = TACHE) -> list[tuple[Path, bool]]:
    """`(chemin, prunable)` de chaque worktree que Git enregistre sur la branche de la tâche."""
    trouves = []
    for bloc in _git(racine, "worktree", "list", "--porcelain").split("\n\n"):
        lignes = bloc.splitlines()
        if f"branch refs/heads/{branche_de_tache(tache_id)}" not in lignes:
            continue
        chemin = next(ligne.removeprefix("worktree ") for ligne in lignes)
        trouves.append((Path(chemin), any(ligne.startswith("prunable") for ligne in lignes)))
    return trouves


def _coquilles(racine: Path, tache_id: str = TACHE) -> list[Path]:
    """Les worktrees de la tâche que Git enregistre et dont le dossier n'a plus de `.git`."""
    return [
        chemin
        for chemin, _ in _worktrees_de_la_branche(racine, tache_id)
        if chemin.exists() and not (chemin / ".git").exists()
    ]


def _coquille_d_avant_le_correctif(projet: Projet) -> Path:
    """Ce que #1447 a trouvé dans `%TEMP%` : un worktree sauvé, puis effacé jusqu'à la butée."""
    with espace_de_travail(projet, tache_id=TACHE, keep=True) as ws:
        coquille = ws.path
    _ecrit_avec_ses_dependances(coquille)
    assert sauver_le_travail_en_vol(projet, TACHE) is True
    with pytest.raises(_Tue):
        _effacer_jusqu_a_la_butee(coquille)
    assert _coquilles(Path(projet.racine)) == [coquille]
    return coquille


@pytest.mark.usefixtures("_temporaire_isole")
def test_un_demontage_tue_pendant_qu_il_efface_ne_laisse_pas_de_coquille(
    tmp_path: Path, _mort_pendant_l_effacement: list[bool]
) -> None:
    """Le défaut de #1447 : l'hôte achevé au milieu de `node_modules/` laissait un dossier
    sans `.git` que Git tenait encore pour le worktree de la branche — et S12 s'y arrêtait.

    Le worktree est désormais **intact ou retiré** à tout instant : ce que la mort laisse
    n'est plus à son adresse, ni enregistré chez Git.
    """
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    with pytest.raises(_Tue):
        with espace_de_travail(projet, tache_id=TACHE) as ws:
            chemin = ws.path
            _ecrit_avec_ses_dependances(chemin)
            _mort_pendant_l_effacement.append(True)

    assert _coquilles(racine) == []
    assert not chemin.exists()
    assert en_cours_d_ecriture(racine, branche_de_tache(TACHE)) == ()
    # Le travail, lui, était sur sa branche avant que rien ne s'efface.
    assert _fichiers_de_la_branche(racine) == {"package.json", "src/recettes.ts", "tsconfig.json"}


@pytest.mark.usefixtures("_temporaire_isole")
def test_ce_qu_une_mort_laisse_en_effacant_part_au_ramassage(
    tmp_path: Path, _mort_pendant_l_effacement: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le reste d'un effacement coupé n'est plus un worktree : le ramassage l'emporte
    dès que son hôte est mort, au lieu de le garder comme du travail à sauver."""
    projet = _projet(tmp_path)
    with pytest.raises(_Tue):
        with espace_de_travail(projet, tache_id=TACHE) as ws:
            parent = ws.path.parent
            _ecrit_avec_ses_dependances(ws.path)
            _mort_pendant_l_effacement.append(True)
    assert any(parent.iterdir())  # le reste est là, au répertoire temporaire

    monkeypatch.setattr(ramassage, "pid_vivant", lambda _pid: False)
    assert ramassage.raison_de_garder(parent) is None


@pytest.mark.usefixtures("_temporaire_isole")
def test_une_mort_entre_l_ecart_et_l_oubli_ne_laisse_rien_de_lisible_a_moitie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'autre instant du démontage : le worktree a quitté son adresse, Git ne l'a pas
    encore oublié. Son enregistrement est `prunable`, et rien ne s'y arrête — ni
    l'oracle de S12, ni la sauvegarde de l'extinction, ni le montage suivant."""
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    arme: list[bool] = []
    git = espace_projet._git

    def _git_mortel(depot: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        if arme and arguments[:2] == ("worktree", "prune"):
            raise _Tue
        return git(depot, *arguments)

    monkeypatch.setattr(espace_projet, "_git", _git_mortel)
    with pytest.raises(_Tue):
        with espace_de_travail(projet, tache_id=TACHE) as ws:
            chemin = ws.path
            _ecrit_avec_ses_dependances(chemin)
            arme.append(True)
    arme.clear()

    assert not chemin.exists()
    assert _worktrees_de_la_branche(racine) == [(chemin, True)]
    assert en_cours_d_ecriture(racine, branche_de_tache(TACHE)) == ()
    assert sauver_le_travail_en_vol(projet, TACHE) is False
    with espace_de_travail(projet, tache_id=TACHE) as ws:
        assert (ws.path / "src" / "recettes.ts").is_file()


@pytest.mark.usefixtures("_temporaire_isole")
def test_un_worktree_qu_un_process_tient_encore_reste_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un process vit encore dans le worktree (Windows refuse alors de le déplacer) : il
    reste **intact et monté**, comme celui d'une tâche tuée avant son démontage, et
    c'est le montage suivant de la tâche qui le libère — jamais une demi-mesure."""
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    renommer = Path.rename

    def _refuse(source: Path, cible: Path) -> Path:
        if source.name == TACHE:
            raise PermissionError(32, "Le processus ne peut pas accéder au fichier")
        return renommer(source, cible)

    monkeypatch.setattr(Path, "rename", _refuse)
    monkeypatch.setattr(espace_projet, "_PAUSES_ECART_S", ())
    with espace_de_travail(projet, tache_id=TACHE) as ws:
        chemin = ws.path
        _ecrit_avec_ses_dependances(chemin)

    assert _worktrees_de_la_branche(racine) == [(chemin, False)]
    assert (chemin / ".git").is_file() and (chemin / BUTEE).is_dir()
    assert _en_attente(chemin) == ""  # son travail est sur la branche
    assert _fichiers_de_la_branche(racine) == {"package.json", "src/recettes.ts", "tsconfig.json"}

    monkeypatch.setattr(Path, "rename", renommer)
    with espace_de_travail(projet, tache_id=TACHE) as ws:
        assert (ws.path / "src" / "recettes.ts").is_file()
    assert not chemin.exists()
    assert _worktrees_de_la_branche(racine) == []


@pytest.mark.usefixtures("_temporaire_isole")
def test_une_coquille_d_avant_le_correctif_n_arrete_ni_l_oracle_ni_la_sauvegarde(
    tmp_path: Path,
) -> None:
    """Le second critère : un poste antérieur au correctif en porte déjà. L'oracle de S12
    lisait `git status` dedans (« not a git repository »), et la sauvegarde de
    l'extinction y lançait `git add -A` — dans le premier dépôt trouvé en remontant."""
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    _coquille_d_avant_le_correctif(projet)
    avant = _git(racine, "rev-parse", branche_de_tache(TACHE)).strip()

    assert en_cours_d_ecriture(racine, branche_de_tache(TACHE)) == ()
    assert sauver_le_travail_en_vol(projet, TACHE) is False
    assert _git(racine, "rev-parse", branche_de_tache(TACHE)).strip() == avant


@pytest.mark.usefixtures("_temporaire_isole")
def test_une_coquille_d_avant_le_correctif_n_arrete_pas_le_montage_et_part_au_ramassage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La tâche se remonte sur son travail, et la coquille — désormais oubliée de Git —
    quitte le disque au ramassage dès que l'hôte qui l'a laissée est mort."""
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    coquille = _coquille_d_avant_le_correctif(projet)

    with espace_de_travail(projet, tache_id=TACHE) as ws:
        assert (ws.path / "src" / "recettes.ts").is_file()
    assert _worktrees_de_la_branche(racine) == []

    monkeypatch.setattr(ramassage, "pid_vivant", lambda _pid: False)
    assert ramassage.raison_de_garder(coquille.parent) is None
