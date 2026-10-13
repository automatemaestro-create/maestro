"""Le miroir de vérification d'un projet non versionné se copie une fois par run (#1497).

Depuis #1496, la vérification d'une tâche sur un projet non versionné joue dans un
**miroir** de la racine (`maestro.sandbox.verification.MiroirDeVerification`). Tenu par
l'espace d'une seule tâche, il se recopiait et se retirait à chaque tâche — 20 s par
tâche sur la racine de S12. Or il se resynchronise avant chaque usage : il vaut pour
toutes les tâches du projet, qui passent déjà une à une dans sa racine.

L'exécuteur en process le garde donc **le temps du run** (`RegistreDesMiroirs`), que la
boucle borne autour de ses tâches (`TaskExecutor.tient_le_run`) : copié à la première
vérification du run, resynchronisé aux suivantes, retiré à la fin — run en échec ou
annulé compris. Un run que personne ne tient — celui d'un worker de file — n'en a pas,
et chaque tâche y garde le sien.

Trois étages :

- la **boucle** : vrai moteur, vrai exécuteur, vrais espaces sur un vrai projet non
  versionné ; seuls l'agent, le planificateur et le vérificateur sont doublés, et les
  copies comptées par un espion sur la naissance du miroir ;
- l'**espace** : un miroir fourni par l'appelant sert l'espace sans partir avec lui ;
- le **registre** : prêt exclusif, retrait différé au retour d'un prêt, runs distincts.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.emplacements import racine_jetable
from maestro.engine import OrchestrationEngine
from maestro.engine.executor import LocalExecutor
from maestro.engine.guardrails import DemandeValidation, Guardrails
from maestro.engine.verification import SYSTEME, VerificateurTaches
from maestro.orchestrator import Orchestrator
from maestro.orchestrator.schema import Task
from maestro.projets import Projet, ProjetStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import espace_de_travail, ramasser
from maestro.sandbox import verification as execution
from maestro.sandbox.ramassage import pid_dans
from maestro.sandbox.verification import MiroirDeVerification, RegistreDesMiroirs
from maestro.telemetry import RunJournal

#: L'interpréteur des doubles de joueur : jamais lancé, il n'a qu'à exister.
_INTERPRETE = ("bash", "-c")

#: Les fichiers que la personne avait déjà dans sa racine : assez pour que la copie
#: complète se distingue d'une resynchronisation.
_FICHIERS_DE_LA_PERSONNE = 12


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` : sous Windows, `AppData` refuserait les racines."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


class _Copies:
    """L'espion des copies : chaque miroir qui naît, et ce que chaque synchronisation copie.

    Un miroir qui naît est une copie **complète** de la racine ; les synchronisations
    suivantes d'un même miroir ne recopient que ce qui a bougé.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.naissances: list[Path] = []
        self.fichiers_copies: list[int] = []
        naitre = MiroirDeVerification._naitre
        copier = MiroirDeVerification._copier

        def naitre_espionne(miroir: MiroirDeVerification, source: Path) -> Path:
            neuf = miroir.chemin is None
            chemin = naitre(miroir, source)
            if neuf:
                self.naissances.append(chemin)
            return chemin

        def copier_espionne(miroir, source, copie, a_copier) -> None:
            self.fichiers_copies.append(len(a_copier))
            copier(miroir, source, copie, a_copier)

        monkeypatch.setattr(MiroirDeVerification, "_naitre", naitre_espionne)
        monkeypatch.setattr(MiroirDeVerification, "_copier", copier_espionne)


@pytest.fixture
def copies(monkeypatch: pytest.MonkeyPatch) -> _Copies:
    return _Copies(monkeypatch)


# --------------------------------------------------------------------------- #
# Doubles : le planificateur, l'agent, le vérificateur et son joueur
# --------------------------------------------------------------------------- #


def _projet_nu(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet **non versionné**, déjà peuplé par la personne."""
    racine = tmp_path / "projets" / "herbier"
    (racine / "src").mkdir(parents=True)
    for n in range(_FICHIERS_DE_LA_PERSONNE):
        (racine / "src" / f"page{n}.ts").write_text(f"export const p = {n}\n", encoding="utf-8")
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer("herbier", racine)
    assert not projet.versionne, projet
    return depot, projet


def _plan(*taches: tuple[str, tuple[str, ...]]) -> str:
    """Le plan que le planificateur factice rend : `(id, dépendances)` par tâche."""
    return json.dumps(
        [
            {
                "id": id_,
                "titre": f"Tâche {id_}",
                "description": f"livrable:{id_}.md — critères : le livrable existe.",
                "competences_requises": ["backend"],
                "format_sortie": "markdown",
                "dependances": list(dependances),
            }
            for id_, dependances in taches
        ],
        ensure_ascii=False,
    )


class _Planificateur(ModelProvider):
    """Rend toujours le même plan."""

    name = "plan-1497"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan

    async def run_agent(self, prompt, **kwargs):  # pragma: no cover
        raise AssertionError("le planificateur ne s'exécute pas outillé")


class _Agent(ModelProvider):
    """Dépose le livrable que sa tâche nomme ; `echoue` lève, `retenue` attend qu'on la relâche."""

    name = "agent-1497"

    def __init__(self, *, echoue: str = "", retenue: str = "") -> None:
        self._echoue = echoue
        self._retenue = retenue
        self.partie = asyncio.Event()
        self.ecrits: list[str] = []

    @property
    def commandes_sur_le_poste(self) -> bool:
        return False  # rien à sonder du poste : les commandes sont doublées

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, **_):
        nom = re.search(r"livrable:([\w./-]+)", prompt)
        assert nom is not None, prompt
        livrable = nom.group(1)
        if livrable == self._retenue:
            self.partie.set()
            await asyncio.Event().wait()  # jusqu'à l'annulation du run
        if livrable == self._echoue:
            raise RuntimeError("l'agent est tombé")
        (Path(workspace) / livrable).write_text(f"fait par {livrable}\n", encoding="utf-8")
        self.ecrits.append(livrable)
        return "Fait."


class _Verificateur(ModelProvider):
    """Rend les contrôles établis — un contrôle, une commande."""

    name = "verificateur-1497"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        assert system_prompt == SYSTEME
        return json.dumps({"controles": [{"critere": "le livrable existe", "commande": "ls"}]})

    async def run_agent(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("le vérificateur ne lance pas d'agent")


class _Joueur:
    """Joue chaque contrôle avec succès, et retient **où** il a été joué."""

    def __init__(self) -> None:
        self.lieux: list[Path] = []

    def __call__(self, commande, cwd, *, interprete, delai_s) -> execution.Execution:
        self.lieux.append(Path(cwd))
        return execution.Execution(code=0, sortie="ok\n", duree_s=0.01)


class _Validateur:
    """L'accord d'écriture continue (#706) : toujours oui."""

    async def __call__(self, demande: DemandeValidation) -> bool:
        return True


def _executeur(agent: ModelProvider, depot: ProjetStore, joueur: _Joueur) -> LocalExecutor:
    return LocalExecutor(
        agent,
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(agent, DEVELOPER_PROFILE)},
        projets=depot,
        guardrails=Guardrails(validateur=_Validateur()),
        verificateur=VerificateurTaches(
            _Verificateur(), modele="double", joueur=joueur, interprete=_INTERPRETE
        ),
    )


def _moteur(agent: ModelProvider, depot: ProjetStore, joueur: _Joueur, plan: str):
    return OrchestrationEngine(
        agent,
        Orchestrator(_Planificateur(plan), model="factice"),
        executor=_executeur(agent, depot, joueur),
    )


def _tache(id_: str, projet_id: str) -> Task:
    return Task(
        id=id_,
        titre=f"Tâche {id_}",
        description=f"livrable:{id_}.md — critères : le livrable existe.",
        competences_requises=("backend",),
        format_sortie="markdown",
        projet_id=projet_id,
    )


# --------------------------------------------------------------------------- #
# La boucle : une copie par run, retirée à la fin quoi qu'il arrive
# --------------------------------------------------------------------------- #


def test_deux_taches_d_un_run_ne_copient_la_racine_qu_une_fois(
    tmp_path: Path, copies: _Copies
) -> None:
    """La seconde tâche se vérifie dans le miroir que la première a fait copier, resynchronisé :
    une seule copie complète de la racine, et un retrait à la fin du run."""
    depot, projet = _projet_nu(tmp_path)
    agent, joueur = _Agent(), _Joueur()
    moteur = _moteur(agent, depot, joueur, _plan(("t1", ()), ("t2", ("t1",))))

    rapport = asyncio.run(moteur.run("deux pages", projet_id=projet.id))

    assert all(r.ok for r in rapport.resultats), [r.erreur for r in rapport.resultats]
    assert agent.ecrits == ["t1.md", "t2.md"]
    assert len(copies.naissances) == 1
    miroir = copies.naissances[0]
    # Les deux tâches se sont vérifiées au même endroit, et ce n'est pas la racine.
    assert set(joueur.lieux) == {miroir}
    assert miroir != Path(projet.racine)
    # La première vérification copie toute la racine ; la seconde, le seul livrable
    # que la seconde tâche y a ajouté.
    assert copies.fichiers_copies[0] >= _FICHIERS_DE_LA_PERSONNE + 1
    assert copies.fichiers_copies[1:] == [1]
    # Le run fini, le miroir est retiré.
    assert not miroir.parent.exists()


def test_le_miroir_du_run_est_retire_quand_une_tache_echoue(
    tmp_path: Path, copies: _Copies
) -> None:
    """Un run qui finit en échec retire son miroir comme un run vert."""
    depot, projet = _projet_nu(tmp_path)
    agent, joueur = _Agent(echoue="t2.md"), _Joueur()
    moteur = _moteur(agent, depot, joueur, _plan(("t1", ()), ("t2", ("t1",))))

    rapport = asyncio.run(moteur.run("deux pages", projet_id=projet.id))

    assert [r.ok for r in rapport.resultats] == [True, False]
    assert len(copies.naissances) == 1
    assert not copies.naissances[0].parent.exists()


def test_le_miroir_du_run_est_retire_quand_le_run_est_annule(
    tmp_path: Path, copies: _Copies
) -> None:
    """Un run annulé pendant sa seconde tâche — l'extinction, l'arrêt d'une personne — retire
    le miroir que la première a fait copier."""
    depot, projet = _projet_nu(tmp_path)
    agent, joueur = _Agent(retenue="t2.md"), _Joueur()
    moteur = _moteur(agent, depot, joueur, _plan(("t1", ()), ("t2", ("t1",))))

    async def scenario() -> None:
        run = asyncio.create_task(moteur.run("deux pages", projet_id=projet.id))
        await asyncio.wait_for(agent.partie.wait(), timeout=30)
        assert len(copies.naissances) == 1 and copies.naissances[0].exists()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    asyncio.run(scenario())

    assert agent.ecrits == ["t1.md"]
    assert not copies.naissances[0].parent.exists()


def test_sans_run_tenu_chaque_tache_garde_son_miroir(tmp_path: Path, copies: _Copies) -> None:
    """Le régime d'un worker de file : l'exécuteur reçoit des tâches sans que personne ne tienne
    leur run, et chacune a son miroir, retiré avec son espace — rien ne s'accumule."""
    depot, projet = _projet_nu(tmp_path)
    agent, joueur = _Agent(), _Joueur()
    executeur = _executeur(agent, depot, joueur)
    journal = RunJournal(run_id="run-worker-1497")

    async def deux_taches() -> None:
        for id_ in ("t1", "t2"):
            resultat = await executeur.execute(_tache(id_, projet.id), (), journal)
            assert resultat.ok, resultat.erreur

    asyncio.run(deux_taches())

    assert len(copies.naissances) == 2
    assert [p for p in copies.naissances if p.parent.exists()] == []


# --------------------------------------------------------------------------- #
# L'espace : un miroir fourni le sert sans partir avec lui
# --------------------------------------------------------------------------- #


def _projet(racine: Path) -> Projet:
    return Projet(id="prj-0000beef", nom="Herbier", racine=racine.as_posix(), vcs=None)


def test_l_espace_joue_dans_le_miroir_fourni_et_ne_le_retire_pas(tmp_path: Path) -> None:
    racine = tmp_path / "herbier"
    racine.mkdir()
    (racine / "index.ts").write_text("export {}\n", encoding="utf-8")
    miroir = MiroirDeVerification(racine)
    try:
        with espace_de_travail(_projet(racine), tache_id="t1", miroir=miroir) as ws:
            with ws.sans_trace() as lieu:
                assert lieu.chemin == miroir.chemin and lieu.chemin is not None
        assert miroir.chemin is not None and miroir.chemin.exists()
    finally:
        miroir.fermer()


def test_un_miroir_d_une_autre_racine_n_est_pas_pris(tmp_path: Path) -> None:
    """Un miroir qui ne reflète pas cette racine ferait juger une autre arborescence :
    l'espace a alors le sien, qui part avec lui."""
    racine, ailleurs = tmp_path / "herbier", tmp_path / "ailleurs"
    racine.mkdir()
    ailleurs.mkdir()
    etranger = MiroirDeVerification(ailleurs)
    with espace_de_travail(_projet(racine), tache_id="t1", miroir=etranger) as ws:
        with ws.sans_trace() as lieu:
            assert lieu.chemin is not None
            propre = lieu.chemin
    assert etranger.chemin is None
    assert not propre.parent.exists()


# --------------------------------------------------------------------------- #
# Le registre : un run tenu, un prêt à la fois, un retrait qui attend le prêt
# --------------------------------------------------------------------------- #


def _racine(tmp_path: Path) -> Path:
    racine = tmp_path / "racine"
    racine.mkdir()
    (racine / "a.txt").write_text("a\n", encoding="utf-8")
    return racine


def test_un_run_que_personne_ne_tient_n_a_pas_de_miroir(tmp_path: Path) -> None:
    with RegistreDesMiroirs().prete("run-x", _racine(tmp_path)) as miroir:
        assert miroir is None


def test_un_miroir_ne_sert_qu_un_espace_a_la_fois(tmp_path: Path) -> None:
    """Prêté, il est refusé au suivant — une réalisation détachée par un time-out (#64) ne le
    partage pas avec la tâche d'après —, puis rendu, il revient le même."""
    registre, racine = RegistreDesMiroirs(), _racine(tmp_path)
    registre.ouvrir("run-1")
    with registre.prete("run-1", racine) as premier:
        assert premier is not None
        with registre.prete("run-1", racine) as second:
            assert second is None
    with registre.prete("run-1", racine) as revenu:
        assert revenu is premier
    registre.retirer("run-1")


def test_un_retrait_pendant_un_pret_attend_son_retour(tmp_path: Path) -> None:
    registre, racine = RegistreDesMiroirs(), _racine(tmp_path)
    registre.ouvrir("run-1")
    with registre.prete("run-1", racine) as miroir:
        assert miroir is not None
        chemin = miroir.synchroniser()
        registre.retirer("run-1")
        assert chemin.exists()
    assert not chemin.parent.exists()
    with registre.prete("run-1", racine) as apres:
        assert apres is None


def test_chaque_run_a_son_miroir_et_le_retire_seul(tmp_path: Path) -> None:
    registre, racine = RegistreDesMiroirs(), _racine(tmp_path)
    registre.ouvrir("run-1")
    registre.ouvrir("run-2")
    with registre.prete("run-1", racine) as un, registre.prete("run-2", racine) as deux:
        assert un is not None and deux is not None and un is not deux
        chemin_un, chemin_deux = un.synchroniser(), deux.synchroniser()
    registre.retirer("run-1")
    assert not chemin_un.parent.exists() and chemin_deux.exists()
    registre.retirer("run-2")
    assert not chemin_deux.parent.exists()


def test_un_run_tenu_deux_fois_garde_son_miroir_jusqu_a_la_derniere_sortie(
    tmp_path: Path,
) -> None:
    registre, racine = RegistreDesMiroirs(), _racine(tmp_path)
    registre.ouvrir("run-1")
    registre.ouvrir("run-1")
    with registre.prete("run-1", racine) as miroir:
        assert miroir is not None
        chemin = miroir.synchroniser()
    registre.retirer("run-1")
    assert chemin.exists()
    registre.retirer("run-1")
    assert not chemin.parent.exists()


def _mort() -> int:
    """Le pid d'un process qui a vécu et ne vit plus."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_un_miroir_laisse_par_un_process_mort_part_au_ramassage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le filet : un process qui meurt avant la fin du run ne retire pas son miroir. Né sous la
    racine jetable et marqué du pid de son process, il est emporté par le ramassage."""
    temp = tmp_path / "temp"
    temp.mkdir()  # un répertoire temporaire qui n'existe pas n'est pas retenu
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(temp))
    registre, racine = RegistreDesMiroirs(), _racine(tmp_path)
    registre.ouvrir("run-1")
    with registre.prete("run-1", racine) as miroir:
        assert miroir is not None
        parent = miroir.synchroniser().parent
    assert parent.parent == racine_jetable() == temp / "maestro"
    assert pid_dans(parent.name) == os.getpid()
    # Le process meurt avant `retirer` : c'est désormais le pid d'un process mort.
    orphelin = parent.with_name(parent.name.replace(f"pid{os.getpid()}-", f"pid{_mort()}-"))
    parent.rename(orphelin)

    ramasser(jetables=(racine_jetable(),), environnement={})  # la suite l'éteint (conftest)

    assert not orphelin.exists()
