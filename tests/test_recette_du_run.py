"""La fin d'un run vérifie les critères du brief en les exécutant, et en rend la recette (#1530).

Sur p5 (run `fea601a5c629`), la dernière tâche du plan, `recette-finale`, rejouait toute
l'application pour confronter les 8 critères du brief : 12 min 50 s du chemin critique et
1,633 $, pour refaire ce que la vérification de chaque tâche venait de faire. La recette est
désormais un geste de Maestro, à la fin du run (`maestro.engine.recette`) : chaque critère
du brief est confronté au livrable intégré par le vérificateur des tâches, et la recette
s'écrit dans le projet, se consigne au journal et se résume dans le récit de fin.

Aucun appel réseau : le planificateur, l'agent et le vérificateur sont des doubles, et les
commandes de contrôle sont jouées par un joueur factice qui retient **où** il joue.

Critères couverts :

① chaque critère du brief reçoit un verdict — tenu, non tenu, ou non vérifiable avec sa
  raison — et sa preuve exécutée ; un critère non tenu se dit, preuve à l'appui, sans être
  coché ; la recette s'écrit dans le projet et se résume dans le récit de fin ;
② le playbook du Chef de projet ne demande plus de tâche qui ne ferait que vérifier, et un
  plan qui en porte une se relit.

La preuve sur le réel (critère 3 du ticket, p5 rejoué) n'est pas ici : elle se consigne sur
le ticket.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.controltower import ControlTowerState
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.recit import SYSTEME as SYSTEME_RECIT
from maestro.controltower.recit import contexte_du_recit
from maestro.engine import MODE_BRIEF_AUTO, OrchestrationEngine
from maestro.engine.executor import LocalExecutor
from maestro.engine.guardrails import DemandeValidation, Guardrails
from maestro.engine.recette import (
    STATUT_RECETTE_IMPOSSIBLE,
    STATUT_RECETTE_NON_TENUE,
    STATUT_RECETTE_TENUE,
    RecetteDuRun,
    chemin_de_la_recette,
    ecrire_dans_le_projet,
    par_critere,
    recetter,
)
from maestro.engine.verification import (
    CONSTAT_NON_JOUE,
    CONSTAT_NON_TENU,
    CONSTAT_TENU,
    SYSTEME,
    Constat,
    Livraison,
    VerificateurTaches,
)
from maestro.orchestrator import BRIEF_SYSTEM_PROMPT, Brief, Orchestrator
from maestro.orchestrator.prompt import ORCHESTRATOR_SYSTEM_PROMPT
from maestro.projets import Projet, ProjetStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution
from maestro.sandbox.en_place import ATELIERS_RESERVES, chemin_atelier
from maestro.telemetry import ETAPE_RECETTE, RunJournal, StepUsage
from maestro.telemetry.costs import RunCost
from maestro.telemetry.usage import report_usage

#: L'interpréteur des doubles de joueur : jamais lancé, il n'a qu'à exister.
_INTERPRETE = ("bash", "-c")

#: Les critères d'acceptation du brief : l'un se constate en exécutant et tient, l'autre
#: s'exécute et ne tient pas, le troisième se lit, le dernier ne reçoit aucun contrôle.
CRITERES = (
    "La page d'accueil existe",
    "Les tests du projet passent",
    "Le README dit comment lancer l'application",
    "L'application répond en moins de 200 ms",
)

BRIEF = Brief.from_dict(
    {
        "objectif": "Un herbier en ligne",
        "perimetre": ["Deux pages"],
        "hors_perimetre": ["Un compte utilisateur"],
        "criteres_acceptation": list(CRITERES),
        "contraintes": [],
        "hypotheses": [],
        "questions": [],
    }
)

#: Ce que le vérificateur établit pour la recette — numéros recopiés, ordre bousculé,
#: texte paraphrasé : c'est le numéro qui rattache un contrôle à son critère.
CONTROLES_DE_RECETTE = {
    "controles": [
        {"critere": "C2 — les tests passent", "commande": "npm test"},
        {"critere": "C1 — La page d'accueil existe", "commande": "test -f accueil.md"},
        {
            "critere": "C3 — Le README dit comment lancer l'application",
            "lecture": "le README nomme la commande de lancement",
            "tenu": True,
            "preuve": "README.md : « npm start »",
        },
        {"critere": "C9 — un critère que le brief n'a pas", "commande": "true"},
    ]
}

#: La sortie de la commande qui ne tient pas — la preuve que la recette doit porter.
_ECHEC_DES_TESTS = "FAIL accueil.test.js\n  attendu 2 pages, trouvé 1"


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` : sous Windows, `AppData` refuserait les racines."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _Joueur:
    """Joue chaque contrôle — `npm test` échoue — et retient où et quoi."""

    def __init__(self) -> None:
        self.joues: list[tuple[str, Path]] = []

    def __call__(self, commande, cwd, *, interprete, delai_s) -> execution.Execution:
        self.joues.append((commande, Path(cwd)))
        if commande == "npm test":
            return execution.Execution(code=1, sortie=_ECHEC_DES_TESTS, duree_s=0.01)
        return execution.Execution(code=0, sortie="ok\n", duree_s=0.01)


class _Verificateur(ModelProvider):
    """Le vérificateur : un contrôle par tâche, les contrôles numérotés pour la recette.

    Il reconnaît la recette à la tâche qu'elle lui fait vérifier (son titre), et signale
    l'usage de cet appel-là — c'est ce que le grand livre doit porter sur sa ligne.
    """

    name = "verificateur-1530"

    def __init__(self, *, recette: dict | None = None, panne: bool = False) -> None:
        self._recette = recette if recette is not None else CONTROLES_DE_RECETTE
        self._panne = panne
        self.recettes: list[str] = []
        self.contre_expertises: list[str] = []
        self.taches: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        assert system_prompt == SYSTEME
        if "Recette du run" in prompt:
            if self._panne:
                raise ConnectionError("fournisseur injoignable")
            if "n'ont pas tenu" in prompt:
                # La contre-expertise de la commande qui n'a pas tenu : le défaut est au
                # livrable, rien ne se réécrit.
                self.contre_expertises.append(prompt)
                report_usage(StepUsage(appels=1, tokens_entree=300, cout_usd=0.05))
                return json.dumps({"commandes": []})
            self.recettes.append(prompt)
            report_usage(StepUsage(appels=1, tokens_entree=900, cout_usd=0.25))
            return json.dumps(self._recette, ensure_ascii=False)
        self.taches.append(prompt)
        return json.dumps({"controles": [{"critere": "le livrable existe", "commande": "ls"}]})


class _Cadrage(ModelProvider):
    """Le Chef de projet : le brief au cadrage, le plan à la planification."""

    name = "cadrage-1530"

    def __init__(self, plan: str) -> None:
        self._plan = plan

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if system_prompt == BRIEF_SYSTEM_PROMPT:
            return json.dumps(BRIEF.to_dict(), ensure_ascii=False)
        return self._plan


class _Agent(ModelProvider):
    """Dépose le livrable que sa tâche nomme ; `echoue` lève."""

    name = "agent-1530"

    def __init__(self, *, echoue: tuple[str, ...] = ()) -> None:
        self._echoue = echoue

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
        if nom.group(1) in self._echoue:
            raise RuntimeError("l'agent est tombé")
        (Path(workspace) / nom.group(1)).write_text("fait\n", encoding="utf-8")
        return "Fait."


class _Validateur:
    async def __call__(self, demande: DemandeValidation) -> bool:
        return True


def _plan(*ids: str) -> str:
    return json.dumps(
        [
            {
                "id": id_,
                "titre": f"Tâche {id_}",
                "description": f"livrable:{id_}.md — critères : le livrable existe.",
                "competences_requises": ["backend"],
                "format_sortie": "markdown",
                "dependances": [],
            }
            for id_ in ids
        ]
    )


def _projet_nu(tmp_path: Path) -> tuple[ProjetStore, Projet]:
    racine = tmp_path / "projets" / "herbier"
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# Herbier\n\nnpm start\n", encoding="utf-8")
    depot = ProjetStore(tmp_path / "depot")
    projet = depot.creer("herbier", racine)
    assert not projet.versionne, projet
    return depot, projet


def _moteur(
    depot: ProjetStore, verificateur: _Verificateur, joueur: _Joueur, *, agent: _Agent
) -> OrchestrationEngine:
    executeur = LocalExecutor(
        agent,
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(agent, DEVELOPER_PROFILE)},
        projets=depot,
        guardrails=Guardrails(validateur=_Validateur()),
        verificateur=VerificateurTaches(
            verificateur, modele="double", joueur=joueur, interprete=_INTERPRETE
        ),
    )
    return OrchestrationEngine(
        agent,
        Orchestrator(_Cadrage(_plan("accueil", "herbier")), model="chef-de-projet"),
        executor=executeur,
    )


def _livraison(espace: Path | None) -> Livraison:
    return Livraison(sortie="Les tâches du run sont soldées.", espace=espace)


# --------------------------------------------------------------------------- #
# ① Chaque critère reçoit un verdict et sa preuve — le mécanisme de la vérification
# --------------------------------------------------------------------------- #


def test_chaque_critere_du_brief_recoit_un_verdict_et_sa_preuve_executee(tmp_path: Path) -> None:
    """Tenu par une commande jouée, non tenu avec la sortie qui le prouve, tenu à la lecture,
    et non vérifiable — avec sa raison — pour le critère qui n'a reçu aucun contrôle."""
    joueur = _Joueur()
    verificateur = VerificateurTaches(
        _Verificateur(), modele="double", joueur=joueur, interprete=_INTERPRETE
    )

    recette = asyncio.run(
        recetter(verificateur, CRITERES, _livraison(tmp_path), modele="chef", brief=BRIEF)
    )

    accueil, tests, readme, latence = recette.constats
    assert [c.critere for c in recette.constats] == list(CRITERES)
    assert (accueil.etat, accueil.commande, accueil.code) == (CONSTAT_TENU, "test -f accueil.md", 0)
    assert (tests.etat, tests.commande, tests.code) == (CONSTAT_NON_TENU, "npm test", 1)
    assert _ECHEC_DES_TESTS.splitlines()[-1] in tests.preuve
    assert (readme.etat, readme.preuve) == (CONSTAT_TENU, "README.md : « npm start »")
    assert latence.etat == CONSTAT_NON_JOUE
    assert "aucun contrôle" in latence.preuve
    assert recette.statut == STATUT_RECETTE_NON_TENUE
    assert recette.resume() == (
        "2/4 critère(s) du brief tenu(s), 1 non tenu(s), 1 non vérifiable(s)"
    )
    # Les commandes ont été jouées là où la livraison le dit. Le contrôle d'un « C9 » que
    # le brief n'a pas est joué comme les autres, mais ne compte pour aucun critère.
    assert {commande for commande, _ in joueur.joues} == {"npm test", "test -f accueil.md", "true"}
    assert {lieu for _, lieu in joueur.joues} == {tmp_path}
    assert len(recette.constats) == len(CRITERES)


def test_le_numero_rattache_un_controle_a_son_critere_jamais_son_rang() -> None:
    """Le format imposé (« C2 — … ») fait foi ; à défaut, le texte recopié tel quel. Un
    numéro hors du brief, un texte inconnu ou un second constat du même critère sont
    écartés — et un critère resté sans constat n'est jamais tenu."""
    constats = (
        Constat(critere="c3 · paraphrase libre", etat=CONSTAT_TENU, preuve="lu"),
        Constat(critere="  La  page D'ACCUEIL existe ", etat=CONSTAT_TENU, preuve="vu"),
        Constat(critere="C3 — doublon", etat=CONSTAT_NON_TENU),
        Constat(critere="C7 — hors du brief", etat=CONSTAT_TENU),
        Constat(critere="un critère inventé", etat=CONSTAT_TENU),
    )

    rattaches = par_critere(CRITERES, constats)

    assert [c.critere for c in rattaches] == list(CRITERES)
    assert [c.etat for c in rattaches] == [
        CONSTAT_TENU,
        CONSTAT_NON_JOUE,
        CONSTAT_TENU,
        CONSTAT_NON_JOUE,
    ]
    assert rattaches[0].preuve == "vu" and rattaches[2].preuve == "lu"


def test_un_verificateur_en_panne_rend_une_recette_non_jouee_qui_le_dit(tmp_path: Path) -> None:
    """Jamais un vert par défaut : chaque critère est non vérifiable, avec la panne pour raison."""
    verificateur = VerificateurTaches(
        _Verificateur(panne=True), modele="double", joueur=_Joueur(), interprete=_INTERPRETE
    )

    recette = asyncio.run(recetter(verificateur, CRITERES, _livraison(tmp_path), modele="chef"))

    assert recette.statut == STATUT_RECETTE_IMPOSSIBLE
    assert recette.tenus == ()
    assert all(c.etat == CONSTAT_NON_JOUE for c in recette.constats)
    assert "fournisseur injoignable" in recette.empechement
    assert "recette non jouée" in recette.resume()


def test_la_recette_ecrite_ne_coche_que_les_criteres_tenus() -> None:
    """Dans le projet, un critère non tenu reste une case vide, sa preuve en regard."""
    recette = RecetteDuRun(
        constats=(
            Constat(critere=CRITERES[0], etat=CONSTAT_TENU, commande="test -f a", code=0),
            Constat(
                critere=CRITERES[1],
                etat=CONSTAT_NON_TENU,
                preuve=_ECHEC_DES_TESTS,
                commande="npm test",
                code=1,
            ),
            Constat(critere=CRITERES[3], etat=CONSTAT_NON_JOUE, preuve="aucune sonde possible"),
        )
    )

    texte = recette.en_markdown(objectif="Un herbier", run_id="run-x", horodatage="aujourd'hui")

    assert "- [x] **C1** — La page d'accueil existe — tenu" in texte
    assert "- [ ] **C2** — Les tests du projet passent — **non tenu**" in texte
    assert "- [ ] **C3** — L'application répond en moins de 200 ms — **non vérifiable**" in texte
    assert texte.count("- [x]") == 1
    assert "Non tenu — `npm test` → code 1" in texte
    assert "attendu 2 pages, trouvé 1" in texte
    assert "aucune sonde possible" in texte


def test_la_recette_traverse_le_journal_sans_rien_perdre() -> None:
    recette = RecetteDuRun(
        constats=(Constat(critere="a", etat=CONSTAT_NON_TENU, preuve="p", commande="c", code=2),),
        fichier="/projet/.maestro/recette/run.md",
    )

    relue = RecetteDuRun.depuis(json.loads(json.dumps(recette.to_dict())))

    assert relue == recette
    assert recette.to_dict()["statut"] == STATUT_RECETTE_NON_TENUE


def test_tout_tenu_est_la_seule_recette_tenue() -> None:
    tenu = Constat(critere="a", etat=CONSTAT_TENU)
    assert RecetteDuRun(constats=(tenu, tenu)).statut == STATUT_RECETTE_TENUE
    assert RecetteDuRun(constats=()).statut == STATUT_RECETTE_IMPOSSIBLE
    assert (
        RecetteDuRun(constats=(tenu, Constat(critere="b", etat=CONSTAT_NON_JOUE))).statut
        == STATUT_RECETTE_IMPOSSIBLE
    )


# --------------------------------------------------------------------------- #
# ① La recette s'écrit dans le projet, au régime du projet
# --------------------------------------------------------------------------- #


def test_dans_un_projet_non_versionne_la_recette_s_ecrit_en_place(tmp_path: Path) -> None:
    _depot, projet = _projet_nu(tmp_path)

    fichier, raison = ecrire_dans_le_projet(projet, "run-1530", "# Recette\n", accorde=False)

    attendu = Path(projet.racine) / ".maestro" / "recette" / "run-1530.md"
    assert (Path(fichier), raison) == (attendu, "")
    assert attendu.read_text(encoding="utf-8") == "# Recette\n"
    assert chemin_de_la_recette("run-1530") == ".maestro/recette/run-1530.md"


def test_un_atelier_de_tache_ne_prend_pas_le_dossier_des_recettes() -> None:
    """Une tâche nommée `recette` (p5 avait `recette-finale`) a son atelier à côté."""
    assert "recette" in ATELIERS_RESERVES
    assert chemin_atelier("recette") != ".maestro/recette"


def _git(racine: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        cwd=racine,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def projet_versionne(tmp_path: Path) -> Projet:
    if shutil.which("git") is None:  # pragma: no cover — la CI porte git (#333)
        pytest.skip("git absent du poste")
    racine = tmp_path / "projets" / "herbier-git"
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# Herbier\n", encoding="utf-8")
    assert _git(racine, "init", "-q", "-b", "main").returncode == 0
    assert _git(racine, "add", "-A").returncode == 0
    assert _git(racine, "commit", "-q", "-m", "init").returncode == 0
    projet = ProjetStore(tmp_path / "depot").creer("herbier-git", racine)
    assert projet.versionne, projet
    return projet


def test_dans_un_projet_versionne_la_recette_est_commitee_sous_l_accord_du_run(
    projet_versionne: Projet,
) -> None:
    """Commitée seule sur la branche de travail : la racine reste propre, et la fusion
    du run suivant n'est pas refusée pour une racine « occupée »."""
    racine = Path(projet_versionne.racine)

    fichier, raison = ecrire_dans_le_projet(
        projet_versionne, "run-1530", "# Recette\n", accorde=True
    )

    assert raison == ""
    assert Path(fichier) == racine / ".maestro" / "recette" / "run-1530.md"
    assert _git(racine, "status", "--porcelain").stdout == ""
    assert _git(racine, "log", "-1", "--format=%s").stdout.strip() == (
        "Maestro : recette du run run-1530"
    )
    assert _git(racine, "show", "--name-only", "--format=", "HEAD").stdout.split() == [
        ".maestro/recette/run-1530.md"
    ]


def test_sans_accord_un_projet_versionne_ne_recoit_pas_la_recette(
    projet_versionne: Projet,
) -> None:
    racine = Path(projet_versionne.racine)

    fichier, raison = ecrire_dans_le_projet(
        projet_versionne, "run-1530", "# Recette\n", accorde=False
    )

    assert fichier == ""
    assert "aucun accord d'écriture" in raison
    assert not (racine / ".maestro").exists()
    assert _git(racine, "status", "--porcelain").stdout == ""


def test_une_racine_occupee_n_est_pas_commitee_sous_les_pieds_de_la_personne(
    projet_versionne: Projet,
) -> None:
    racine = Path(projet_versionne.racine)
    (racine / "brouillon.txt").write_text("en cours\n", encoding="utf-8")

    fichier, raison = ecrire_dans_le_projet(
        projet_versionne, "run-1530", "# Recette\n", accorde=True
    )

    assert fichier == ""
    assert "changements en cours" in raison
    assert not (racine / ".maestro").exists()


# --------------------------------------------------------------------------- #
# ① À la fin du run : le moteur, avec le modèle doublé
# --------------------------------------------------------------------------- #


def test_a_la_fin_du_run_chaque_critere_du_brief_est_verifie_sur_le_livrable_integre(
    tmp_path: Path,
) -> None:
    """Le chemin d'un vrai run : cadrage, plan de deux tâches, puis la recette du run —
    jouée dans le miroir de la racine, toutes les tâches réunies, jamais dans la racine ;
    consignée au journal avec sa durée et son coût sur sa propre ligne ; écrite dans le
    projet ; et rendue avec le rapport."""
    depot, projet = _projet_nu(tmp_path)
    verificateur, joueur = _Verificateur(), _Joueur()
    journal = RunJournal(run_id="run-1530")

    rapport = asyncio.run(
        _moteur(depot, verificateur, joueur, agent=_Agent()).run(
            "Un herbier", journal=journal, projet_id=projet.id, mode_brief=MODE_BRIEF_AUTO
        )
    )

    assert all(r.ok for r in rapport.resultats), [r.erreur for r in rapport.resultats]
    racine = Path(projet.racine)
    # Une seule recette, après les tâches, sur le brief et les deux livrables réunis — et la
    # commande qui n'a pas tenu, contre-expertisée avant d'être crue (#1177).
    assert len(verificateur.recettes) == 1 and len(verificateur.taches) == 2
    assert len(verificateur.contre_expertises) == 1
    prompt = verificateur.recettes[0]
    for rang, critere in enumerate(CRITERES, start=1):
        assert f"C{rang} — {critere}" in prompt
    assert "accueil.md" in prompt and "herbier.md" in prompt
    # Jouée dans le miroir de la racine, comme la vérification des tâches — jamais la racine.
    de_la_recette = {"npm test", "test -f accueil.md"}
    lieux = {lieu for commande, lieu in joueur.joues if commande in de_la_recette}
    assert len(lieux) == 1 and racine not in lieux
    # Le verdict, critère par critère.
    recette = rapport.recette
    assert recette is not None
    assert [c.etat for c in recette.constats] == [
        CONSTAT_TENU,
        CONSTAT_NON_TENU,
        CONSTAT_TENU,
        CONSTAT_NON_JOUE,
    ]
    # Écrite dans le projet, où seul ce qui tient est coché.
    ecrite = racine / ".maestro" / "recette" / "run-1530.md"
    assert Path(recette.fichier) == ecrite
    texte = ecrite.read_text(encoding="utf-8")
    assert "- [ ] **C2** — Les tests du projet passent — **non tenu**" in texte
    assert texte.count("- [x]") == 2
    # Au journal : l'étape du run, son verdict structuré, sa durée et son coût.
    etape = next(r for r in journal.records if r.etape == ETAPE_RECETTE)
    assert etape.statut == STATUT_RECETTE_NON_TENUE
    assert etape.sortie == recette.resume()
    assert etape.verification == recette.to_dict()
    assert etape.usage.duree_ms is not None
    grand_livre = RunCost.depuis_journal(journal)
    assert grand_livre.recette.cout_usd == pytest.approx(0.30)
    assert grand_livre.recette.appels == 2
    assert all(t.tache_id != ETAPE_RECETTE for t in grand_livre.taches)
    # Et le rapport la dit avant les tâches.
    assert "Recette : 2/4 critère(s) du brief tenu(s)" in rapport.synthese()


def test_un_run_sans_brief_n_a_pas_de_recette(tmp_path: Path) -> None:
    depot, projet = _projet_nu(tmp_path)
    verificateur, journal = _Verificateur(), RunJournal()

    rapport = asyncio.run(
        _moteur(depot, verificateur, _Joueur(), agent=_Agent()).run(
            "Un herbier", journal=journal, projet_id=projet.id
        )
    )

    assert rapport.recette is None
    assert verificateur.recettes == []
    assert all(r.etape != ETAPE_RECETTE for r in journal.records)
    assert not (Path(projet.racine) / ".maestro" / "recette").exists()


def test_un_run_dont_aucune_tache_n_aboutit_ne_paie_pas_sa_recette(tmp_path: Path) -> None:
    """Rien d'intégré à confronter : chaque critère est non vérifiable, sans appel au modèle."""
    depot, projet = _projet_nu(tmp_path)
    verificateur, journal = _Verificateur(), RunJournal(run_id="run-vide")

    rapport = asyncio.run(
        _moteur(
            depot, verificateur, _Joueur(), agent=_Agent(echoue=("accueil.md", "herbier.md"))
        ).run("Un herbier", journal=journal, projet_id=projet.id, mode_brief=MODE_BRIEF_AUTO)
    )

    assert rapport.recette is not None
    assert verificateur.recettes == []
    assert rapport.recette.statut == STATUT_RECETTE_IMPOSSIBLE
    assert "aucune tâche du run n'a abouti" in rapport.recette.empechement
    assert RunCost.depuis_journal(journal).recette.cout_usd is None


# --------------------------------------------------------------------------- #
# ① La projection la garde, et le récit de fin la résume
# --------------------------------------------------------------------------- #


def _recette_consignee() -> tuple[RunJournal, RecetteDuRun]:
    recette = RecetteDuRun(
        constats=(
            Constat(critere=CRITERES[0], etat=CONSTAT_TENU, commande="test -f a", code=0),
            Constat(
                critere=CRITERES[1],
                etat=CONSTAT_NON_TENU,
                preuve=_ECHEC_DES_TESTS,
                commande="npm test",
                code=1,
            ),
        ),
        fichier="C:/projets/herbier/.maestro/recette/run-1530.md",
    )
    journal = RunJournal(run_id="run-1530")
    journal.consigne(
        etape=ETAPE_RECETTE,
        nom="Recette du run — les critères du brief",
        agent="orchestrateur",
        role="Orchestrateur",
        statut=recette.statut,
        entree="2 critère(s)",
        sortie=recette.resume(),
        usage=StepUsage(appels=1, cout_usd=0.3, duree_ms=105_000),
        verification=recette.to_dict(),
    )
    return journal, recette


def test_la_projection_garde_la_recette_au_run_et_son_cout_sur_sa_ligne() -> None:
    journal, recette = _recette_consignee()
    state = ControlTowerState()

    for evenement in evenements_depuis_step(journal.records[0].to_dict()):
        state.appliquer(evenement)

    execution_ = state.execution("run-1530")
    assert execution_ is not None
    assert execution_.recette == recette.to_dict()
    assert execution_.to_dict()["recette"] == recette.to_dict()
    assert execution_.cout.recette.cout_usd == pytest.approx(0.3)
    assert execution_.cout.planification.cout_usd is None
    # Une étape du run, jamais une carte : aucune tâche fantôme « recette ».
    assert execution_.cout.taches == ()
    assert state.taches() == []


def test_le_recit_de_fin_resume_la_recette_et_l_offre_en_lien() -> None:
    journal, recette = _recette_consignee()
    state = ControlTowerState()
    for evenement in evenements_depuis_step(journal.records[0].to_dict()):
        state.appliquer(evenement)
    execution_ = state.execution("run-1530")
    assert execution_ is not None

    contexte = contexte_du_recit(state, execution_, None)

    assert "## La recette du run" in contexte
    assert "Verdict : 1/2 critère(s) du brief tenu(s), 1 non tenu(s)." in contexte
    assert "C2 [non tenu] « Les tests du projet passent » — `npm test`" in contexte
    assert "attendu 2 pages, trouvé 1" in contexte
    # Sa page s'offre en lien, comme un fichier du livrable.
    assert f"- {recette.fichier}" in contexte


def test_la_consigne_du_recit_dit_que_la_recette_fait_foi() -> None:
    assert "**la recette**" in SYSTEME_RECIT
    assert "ne présente jamais comme acquis un critère qu'elle ne dit pas tenu" in " ".join(
        SYSTEME_RECIT.split()
    )


# --------------------------------------------------------------------------- #
# ② Le plan ne porte plus de tâche qui ne ferait que vérifier, et se relit
# --------------------------------------------------------------------------- #


def _playbook() -> str:
    return " ".join(ORCHESTRATOR_SYSTEM_PROMPT.split())


def test_le_playbook_ne_demande_plus_de_tache_d_integration_ou_de_recette() -> None:
    """La phrase qui faisait de l'assemblage une tâche est partie ; la règle inverse est écrite,
    avec sa raison — Maestro recette lui-même le livrable intégré à la fin du run."""
    texte = _playbook()

    assert "l'intégration, l'assemblage — est une tâche qui dépend d'eux tous" not in texte
    assert "n'est une tâche que s'il y a quelque chose à **produire**" in texte
    assert "**Aucune tâche pour vérifier.**" in texte
    assert "recette, intégration qui ne fait que rejouer, validation finale, revue" in texte
    assert (
        "à la fin du run, Maestro confronte lui-même chaque critère d'acceptation du brief "
        "au livrable intégré"
    ) in texte


def test_un_plan_qui_porte_une_tache_de_recette_se_relit() -> None:
    """La relecture du plan, dernière étape de la méthode, vise cette tâche-là : elle se
    retire, ou se réécrit autour de ce qu'elle produit. Et les critères de « terminé » le
    redisent."""
    texte = _playbook()

    assert "8. **Relis ton plan**" in texte
    assert "Relis-le aussi contre le point 7" in texte
    assert "se **retire** du plan" in texte
    assert (
        "Aucune tâche n'a pour seul objet de vérifier, recetter ou valider ce que d'autres "
        "livrent"
    ) in texte
