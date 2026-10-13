"""L'agent reçoit les faits de la machine : système, shell, temporaire, programmes (#1403).

Rien ne disait à un agent sur quelle machine il travaillait, et il sondait à l'aveugle
(`uname`, `which pandoc python python3 py`). `maestro.machine` constate ce que Maestro
sait — et lit, par leur `--version`, la version des programmes que l'outillage du projet
appelle (levée bornée de l'interdit de #487, docs/28 §7) ; l'exécuteur le fait une fois
par run, et le runtime le pose dans le message de chaque tâche.

Les sondes sont **doublées** (`joueur`) partout où le verdict ne doit pas dépendre du
poste ; un seul test joue le vrai bash (`@pytest.mark.commandes_jouees`) : c'est la
preuve que la présence et la version se lisent pour de vrai, sans rien supposer du
programme au-delà de `git`, dépendance de la suite (#333).
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import shutil
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.emplacements import repertoire_temporaire
from maestro.engine.executor import LocalExecutor
from maestro.machine import (
    ABSENT,
    NON_SONDE,
    PRESENT,
    TITRE_MACHINE,
    Delais,
    Machine,
    Programme,
    constater,
    programmes_de_l_outillage,
)
from maestro.orchestrator.schema import Task
from maestro.outillage.detection import CHEMIN_MANIFESTE
from maestro.projets.modele import Perimetre, Projet
from maestro.projets.store import ProjetStore
from maestro.providers.base import Credentials, ModelProvider
from maestro.providers.claude import ClaudeProvider
from maestro.sandbox.verification import Execution
from maestro.telemetry import RunJournal

#: Un interpréteur factice : le joueur doublé ne le lance jamais.
BASH = ("/usr/bin/bash", "-c")


class _Joueur:
    """Le poste, doublé : répond à chaque commande selon une table, et retient ce qu'on joue.

    `presents` : programme → sortie de son `--version` (`None` : `--version` en erreur).
    Un programme hors de la table est absent (`type` rend 1). `expirent` : les commandes
    qui ne rendent pas la main dans leur délai.
    """

    def __init__(
        self,
        presents: dict[str, str | None] | None = None,
        *,
        expirent: Sequence[str] = (),
        shell: str = "5.2.37(1)-release",
    ) -> None:
        self.presents = presents or {}
        self.expirent = set(expirent)
        self.shell = shell
        self.joues: list[tuple[str, float]] = []

    def __call__(
        self, commande: str, cwd: Path, *, interprete: Sequence[str], delai_s: float
    ) -> Execution:
        self.joues.append((commande, delai_s))
        if commande in self.expirent:
            return Execution(code=None, sortie="", duree_s=delai_s, expiree=True)
        if commande.startswith("printf"):
            return Execution(code=0, sortie=self.shell, duree_s=0.01)
        if commande.startswith("type -- "):
            nom = commande.removeprefix("type -- ")
            if nom in self.presents:
                return Execution(code=0, sortie=f"{nom} is /usr/bin/{nom}", duree_s=0.01)
            return Execution(code=1, sortie=f"bash: type: {nom}: not found", duree_s=0.01)
        if commande.endswith(" --version"):
            sortie = self.presents.get(commande.removesuffix(" --version"))
            if sortie is None:
                return Execution(code=2, sortie="option inconnue", duree_s=0.01)
            return Execution(code=0, sortie=sortie, duree_s=0.01)
        raise AssertionError(f"commande inattendue : {commande}")

    def commandes(self) -> list[str]:
        return [commande for commande, _ in self.joues]


def _programme(machine: Machine, nom: str) -> Programme:
    (trouve,) = [p for p in machine.programmes if p.nom == nom]
    return trouve


# --------------------------------------------------------------------------- #
# Le constat                                                                    #
# --------------------------------------------------------------------------- #


def test_le_constat_dit_systeme_shell_et_temporaire() -> None:
    """Ce que Maestro sait sans rien deviner : la bibliothèque standard, son bash, son temp."""
    machine = constater((), interprete=BASH, joueur=_Joueur())

    assert platform.system() in machine.systeme
    assert machine.shell == BASH[0]
    assert machine.version_shell == "5.2.37"
    assert machine.temporaire == str(repertoire_temporaire())


def test_presents_absents_et_versions_lues_par_leur_version() -> None:
    """Le critère 1 et le critère 2 : présent ou absent, et la version par `--version`."""
    joueur = _Joueur({"npm": "10.9.2\n", "node": "v22.11.0\n", "pytest": None})

    machine = constater(("npm", "uv", "node", "pytest"), interprete=BASH, joueur=joueur)

    assert [p.nom for p in machine.programmes] == ["npm", "uv", "node", "pytest"]
    assert _programme(machine, "npm") == Programme("npm", PRESENT, "10.9.2")
    assert _programme(machine, "node") == Programme("node", PRESENT, "22.11.0")
    assert _programme(machine, "uv") == Programme("uv", ABSENT)
    # Un `--version` qui rend la main en erreur : présent, version inconnue — jamais devinée.
    assert _programme(machine, "pytest") == Programme("pytest", PRESENT, None)


def test_seul_un_programme_present_est_lance_et_avec_version_seulement() -> None:
    """Ce que la levée de #487 autorise, et rien de plus : `<programme> --version`.

    Un absent n'est jamais lancé ; aucun autre argument que `--version` ; la présence
    se demande par `type`, qui ne lance rien.
    """
    joueur = _Joueur({"npm": "10.9.2"})

    constater(("npm", "uv"), interprete=BASH, joueur=joueur)

    lancements = [c for c in joueur.commandes() if not c.startswith(("type -- ", "printf"))]
    assert lancements == ["npm --version"]
    assert "type -- uv" in joueur.commandes()


def test_chaque_sonde_est_bornee_par_son_delai() -> None:
    """« Sous un délai borné » : aucun appel ne reçoit plus que le délai de son espèce."""
    joueur = _Joueur({"npm": "10.9.2"})
    delais = Delais(sonde_s=1.5, version_s=2.5, total_s=60.0)

    constater(("npm",), interprete=BASH, joueur=joueur, delais=delais)

    for commande, delai in joueur.joues:
        borne = delais.sonde_s if commande.startswith("type -- ") else delais.version_s
        assert 0 < delai <= borne, (commande, delai)


def test_le_temps_alloue_epuise_rend_non_sonde_sans_rien_jouer() -> None:
    """Le délai de l'ensemble : ce qui n'a pas eu le temps n'est pas « absent »."""
    joueur = _Joueur({"npm": "10.9.2"})

    machine = constater(
        ("npm", "uv"), interprete=BASH, joueur=joueur, delais=Delais(total_s=0.0)
    )

    assert joueur.joues == []
    assert {p.etat for p in machine.programmes} == {NON_SONDE}
    assert machine.version_shell is None


def test_une_sonde_qui_ne_rend_pas_la_main_ne_dit_ni_present_ni_absent() -> None:
    """Ne pas savoir n'est pas « absent » : la règle de `Verificateur.sonder` (#1343)."""
    joueur = _Joueur({"npm": "10.9.2"}, expirent=("type -- uv", "npm --version"))

    machine = constater(("npm", "uv"), interprete=BASH, joueur=joueur)

    assert _programme(machine, "uv").etat == NON_SONDE
    assert _programme(machine, "npm") == Programme("npm", PRESENT, None)


def test_sans_bash_rien_n_est_lance_et_le_constat_le_dit() -> None:
    """La garde de la suite retire l'interpréteur (#1160) : c'est le chemin d'un poste sans bash."""
    joueur = _Joueur({"npm": "10.9.2"})

    machine = constater(("npm",), joueur=joueur)

    assert joueur.joues == []
    assert machine.shell == ""
    assert _programme(machine, "npm").etat == NON_SONDE
    assert "aucun bash" in machine.consigne()


def test_un_nom_qui_n_est_pas_un_programme_n_est_jamais_sonde() -> None:
    """Un chemin (`./gradlew`) est un fichier du projet ; un doublon ne se sonde qu'une fois."""
    joueur = _Joueur({"npm": "10.9.2"})

    machine = constater(("./gradlew", "npm", "npm"), interprete=BASH, joueur=joueur)

    assert [p.nom for p in machine.programmes] == ["npm"]
    assert joueur.commandes().count("type -- npm") == 1


@pytest.mark.commandes_jouees
@pytest.mark.skipif(shutil.which("git") is None, reason="git introuvable")
def test_sur_le_vrai_bash_git_est_present_avec_sa_version_et_l_introuvable_absent() -> None:
    """La preuve sans double : le bash des agents répond, `git --version` se lit.

    `git` parce qu'il est une dépendance de la suite (#333) et que le bash des agents
    se trouve à côté de lui sous Windows ; le second nom n'existe nulle part.
    """
    machine = constater(("git", "programme-introuvable-maestro-1403"))

    assert machine.shell, "aucun bash trouvé à côté de git"
    assert machine.version_shell is not None
    git = _programme(machine, "git")
    assert git.etat == PRESENT
    assert git.version is not None and re.fullmatch(r"\d+(\.\d+)+", git.version)
    assert _programme(machine, "programme-introuvable-maestro-1403").etat == ABSENT


# --------------------------------------------------------------------------- #
# La section « La machine »                                                    #
# --------------------------------------------------------------------------- #


def test_la_section_porte_les_quatre_faits() -> None:
    machine = Machine(
        systeme="Windows 11 (10.0.26300, AMD64)",
        temporaire=r"C:\Users\x\AppData\Local\Temp",
        shell=r"C:\Program Files\Git\bin\bash.exe",
        version_shell="5.2.37",
        programmes=(
            Programme("npm", PRESENT, "10.9.2"),
            Programme("uv", ABSENT),
            Programme("pytest", PRESENT),
            Programme("pandoc", NON_SONDE),
        ),
        windows=True,
    )

    section = machine.consigne()

    assert section.startswith(TITRE_MACHINE)
    assert "Windows 11 (10.0.26300, AMD64)" in section
    assert "bash 5.2.37 (Git Bash)" in section
    assert r"`C:\Users\x\AppData\Local\Temp`, que bash appelle `/tmp`" in section
    assert "`npm` 10.9.2" in section
    assert "`uv` — **absent**" in section
    assert "`pytest` — présent, version inconnue" in section
    assert "`pandoc` — non sondé" in section
    # Ce que l'agent n'a plus à faire, et la limite du constat.
    assert "`uname`" in section and "`which`" in section
    assert "installé depuis le début du run" in section


def test_sans_programme_la_section_n_en_invente_aucun() -> None:
    """Sans outillage, aucun programme n'est nommé : la liste ne vient d'aucun catalogue."""
    section = Machine(systeme="Linux 6.8 (x86_64)", temporaire="/tmp").consigne()

    assert "Programmes" not in section
    assert "`/tmp`" in section
    assert "que bash appelle" not in section


# --------------------------------------------------------------------------- #
# Les programmes de l'outillage                                                 #
# --------------------------------------------------------------------------- #


def _projet_outille(racine: Path, commandes: Sequence[str]) -> Projet:
    """Un projet dont le manifeste garde les verdicts de ces commandes (docs/38 §4.1)."""
    manifeste = racine / CHEMIN_MANIFESTE
    manifeste.parent.mkdir(parents=True)
    manifeste.write_text(
        json.dumps(
            {
                "manifeste": 1,
                "entrees": [],
                "verifications": [
                    {"usage": "tester", "commande": c, "etat": "verifiee", "raison": "ok"}
                    for c in commandes
                ],
            }
        ),
        encoding="utf-8",
    )
    return Projet(id="prj-00001403", nom="Démo", racine=racine.as_posix(), perimetre=Perimetre())


def test_les_programmes_sont_lus_sur_les_commandes_du_manifeste(tmp_path: Path) -> None:
    """Le lexique commun lit le verbe de chaque commande, sans catalogue, chacun une fois."""
    projet = _projet_outille(
        tmp_path, ("npm ci", "CI=1 npm test", "uv run pytest", "./gradlew build", "pandoc a.md")
    )

    assert programmes_de_l_outillage(projet) == ("npm", "uv", "pandoc")


def test_sans_projet_ni_manifeste_aucun_programme(tmp_path: Path) -> None:
    sans_manifeste = Projet(
        id="prj-00001404", nom="Nu", racine=tmp_path.as_posix(), perimetre=Perimetre()
    )

    assert programmes_de_l_outillage(None) == ()
    assert programmes_de_l_outillage(sans_manifeste) == ()


# --------------------------------------------------------------------------- #
# Le message de la tâche                                                        #
# --------------------------------------------------------------------------- #

MACHINE = Machine(
    systeme="Linux 6.8 (x86_64)",
    temporaire="/tmp",
    shell="/usr/bin/bash",
    version_shell="5.2.37",
    programmes=(Programme("npm", PRESENT, "10.9.2"),),
)


class _Temoin(ModelProvider):
    """Fournisseur factice : retient le message reçu ; `sur_le_poste` dit où vit l'agent."""

    name = "temoin"

    def __init__(self, *, sur_le_poste: bool = True) -> None:
        self.prompts: list[str] = []
        self._sur_le_poste = sur_le_poste

    @property
    def commandes_sur_le_poste(self) -> bool:
        return self._sur_le_poste

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, workspace, tools, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        return "Fait."


def _execute(provider: ModelProvider, machine: Machine | None) -> str:
    runtime = AgentRuntime(provider, DEVELOPER_PROFILE)
    asyncio.run(runtime.execute("Ajouter un écran", machine=machine))
    return provider.prompts[-1]


def test_le_message_de_la_tache_porte_la_section_la_machine() -> None:
    prompt = _execute(_Temoin(), MACHINE)

    assert MACHINE.consigne() in prompt
    # Après la tâche et les consignes du rôle, avant la consigne finale.
    assert prompt.index("Ajouter un écran") < prompt.index(TITRE_MACHINE)
    assert prompt.index(TITRE_MACHINE) < prompt.index(DEVELOPER_PROFILE.consigne_finale)


def test_sans_constat_le_message_est_celui_d_avant_a_la_ligne_pres() -> None:
    """La section, port d'essai compris (#1491), est tout ce que le constat ajoute."""
    avec = _execute(_Temoin(), MACHINE)
    sans = _execute(_Temoin(), None)
    port = re.search(r"Ton port d'essai\*\* : `(\d+)`", avec)
    assert port is not None

    assert TITRE_MACHINE not in sans
    assert "port d'essai" not in sans
    section = MACHINE.consigne(port_d_essai=int(port.group(1)))
    assert avec.replace(f"\n\n{section}", "") == sans


def test_un_agent_qui_travaille_ailleurs_ne_recoit_pas_les_faits_du_poste() -> None:
    """Le conteneur du mode isolé (#108) : dire Windows à un agent sous Linux, pire que rien."""
    assert TITRE_MACHINE not in _execute(_Temoin(sur_le_poste=False), MACHINE)


def test_le_fournisseur_claude_isole_dit_que_l_agent_travaille_ailleurs() -> None:
    sur_le_poste = ClaudeProvider(Credentials())
    isole = ClaudeProvider(Credentials(), isolation=object())  # type: ignore[arg-type]

    assert sur_le_poste.commandes_sur_le_poste is True
    assert isole.commandes_sur_le_poste is False


# --------------------------------------------------------------------------- #
# Une fois par run                                                              #
# --------------------------------------------------------------------------- #


def _tache(id_: str) -> Task:
    """Une tâche que le routeur confie au développeur, seul rôle outillé monté ici."""
    return Task(
        id=id_,
        titre=f"Tâche {id_}",
        description="d",
        competences_requises=("backend",),
        format_sortie="markdown",
    )


@pytest.fixture
def constats(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Chaque constat demandé par l'exécuteur, avec les programmes qu'il nomme."""
    demandes: list[tuple[str, ...]] = []

    def constater_double(noms: Sequence[str] = ()) -> Machine:
        demandes.append(tuple(noms))
        return MACHINE

    monkeypatch.setattr("maestro.engine.executor.constater", constater_double)
    return demandes


def test_le_constat_est_fait_une_fois_par_run(constats: list[tuple[str, ...]]) -> None:
    """Le critère « sondés une fois par run » : deux tâches simultanées en tête, un constat."""
    provider = _Temoin()
    executeur = LocalExecutor(
        provider, runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(provider, DEVELOPER_PROFILE)}
    )

    async def deux_runs() -> None:
        premier = RunJournal(run_id="run-1403-a")
        await asyncio.gather(
            executeur.execute(_tache("t1"), [], premier),
            executeur.execute(_tache("t2"), [], premier),
        )
        await executeur.execute(_tache("t3"), [], premier)
        await executeur.execute(_tache("t4"), [], RunJournal(run_id="run-1403-b"))

    asyncio.run(deux_runs())

    assert len(constats) == 2  # un par run, pas un par tâche
    assert len(provider.prompts) == 4
    assert all(MACHINE.consigne() in prompt for prompt in provider.prompts)


def test_l_executeur_sonde_les_programmes_de_l_outillage_du_projet_de_la_tache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constats: list[tuple[str, ...]]
) -> None:
    """Du manifeste du projet jusqu'au constat : ce sont ses programmes qu'on demande."""
    # Un `Path.home()` sous `tmp_path` : sous Windows, `valider_racine` refuse `AppData` (#221).
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    _projet_outille(racine, ("npm ci", "uv run pytest"))
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer("Démo", racine)
    provider = _Temoin()
    executeur = LocalExecutor(
        provider,
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(provider, DEVELOPER_PROFILE)},
        projets=depot,
    )
    tache = replace(_tache("t1"), projet_id=projet.id)

    resultat = asyncio.run(executeur.execute(tache, [], RunJournal(run_id="run-1403")))

    assert resultat.ok, resultat.erreur
    assert constats == [("npm", "uv")]


def test_l_executeur_ne_sonde_pas_pour_un_agent_qui_travaille_ailleurs(
    constats: list[tuple[str, ...]],
) -> None:
    provider = _Temoin(sur_le_poste=False)
    executeur = LocalExecutor(
        provider, runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(provider, DEVELOPER_PROFILE)}
    )

    asyncio.run(executeur.execute(_tache("t1"), [], RunJournal(run_id="run-1403")))

    assert constats == []
    assert TITRE_MACHINE not in provider.prompts[0]
