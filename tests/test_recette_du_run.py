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
    recette_en_texte,
    recetter,
)
from maestro.engine.verification import (
    CONSTAT_NON_JOUE,
    CONSTAT_NON_TENU,
    CONSTAT_TENU,
    SYSTEME,
    Constat,
    ConstatAnterieur,
    Livraison,
    VerificateurTaches,
)
from maestro.orchestrator import BRIEF_SYSTEM_PROMPT, Brief, Orchestrator
from maestro.orchestrator.prompt import ORCHESTRATOR_SYSTEM_PROMPT
from maestro.projects import Project, ProjectStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution
from maestro.sandbox.en_place import ATELIERS_RESERVES, chemin_atelier
from maestro.sandbox.projet import empreinte_du_livrable
from maestro.telemetry import STEP_ACCEPTANCE, RunJournal, StepUsage
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
                report_usage(StepUsage(calls=1, input_tokens=300, cost_usd=0.05))
                return json.dumps({"commandes": []})
            self.recettes.append(prompt)
            report_usage(StepUsage(calls=1, input_tokens=900, cost_usd=0.25))
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


def _projet_nu(tmp_path: Path) -> tuple[ProjectStore, Project]:
    racine = tmp_path / "projets" / "herbier"
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# Herbier\n\nnpm start\n", encoding="utf-8")
    depot = ProjectStore(tmp_path / "depot")
    projet = depot.create("herbier", racine)
    assert not projet.versioned, projet
    return depot, projet


def _moteur(
    depot: ProjectStore, verificateur: _Verificateur, joueur: _Joueur, *, agent: _Agent
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


def test_une_abstention_n_est_jamais_dite_comme_un_critere_non_tenu() -> None:
    """#1680 : le message de fin annonçait 3/6 sur trois exécutions manquées. Un critère
    non vérifiable n'est compté ni vert ni rouge (docs/47 § 5), et le résumé le dit."""
    recette = RecetteDuRun(
        constats=(
            Constat(critere="a", etat=CONSTAT_TENU),
            Constat(critere="b", etat=CONSTAT_NON_JOUE, preuve="coupée — …"),
        )
    )

    assert recette.non_tenus == ()
    assert recette.resume() == (
        "1/2 critère(s) du brief tenu(s), 1 non vérifiable(s) — aucun constaté non tenu"
    )


# --------------------------------------------------------------------------- #
# ③ Ce que la vérification vient de tenir ne se rejoue pas (#1680)
# --------------------------------------------------------------------------- #


def _anterieur(
    numero: str,
    critere: str,
    *,
    commande: str = "",
    preuve: str = "1448 passed in 55.02s",
    etat: str = CONSTAT_TENU,
    meme_arbre: bool = True,
) -> ConstatAnterieur:
    return ConstatAnterieur(
        numero=numero,
        tache_id="dev-python",
        titre="Le correctif et ses tests",
        livraison=3,
        constat=Constat(
            critere=critere,
            etat=etat,
            preuve=preuve,
            commande=commande,
            code=(0 if etat == CONSTAT_TENU else 1) if commande else None,
        ),
        meme_arbre=meme_arbre,
    )


class _Recetteur(ModelProvider):
    """Le vérificateur de la recette seule : `reponse` à l'établissement, `contre` ensuite."""

    name = "recetteur-1680"

    def __init__(self, reponse: dict, contre: dict | None = None) -> None:
        self._reponse = reponse
        self._contre = contre if contre is not None else {"commandes": []}
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        assert system_prompt == SYSTEME
        self.prompts.append(prompt)
        if "<non_tenues>" in prompt:
            return json.dumps(self._contre, ensure_ascii=False)
        return json.dumps(self._reponse, ensure_ascii=False)


def _recetter(
    provider: ModelProvider, joueur: _Joueur, espace: Path, anterieurs, criteres=CRITERES
):
    verificateur = VerificateurTaches(
        provider, modele="double", joueur=joueur, interprete=_INTERPRETE
    )
    return asyncio.run(
        recetter(
            verificateur,
            criteres,
            _livraison(espace),
            modele="chef",
            brief=BRIEF,
            anterieurs=anterieurs,
        )
    )


def test_la_recette_reprend_sans_le_rejouer_ce_que_la_verification_a_tenu_sur_le_meme_arbre(
    tmp_path: Path,
) -> None:
    """#1680, run `caf51d06c14f` : la recette rejouait sur le même arbre, en 8 min 53 s et pour
    0,30 $, ce que la vérification venait de tenir, et rendait 3/6 contre 6/6. Un critère
    qu'une vérification a tenu sur un arbre identique est **repris**, sans être rejoué, et
    la recette le dit."""
    anterieurs = (
        _anterieur("V1", "la suite du module passe", commande="pytest tests/test_module.py"),
        _anterieur("V2", "le compte rendu nomme la commande", preuve="README : « npm start »"),
    )
    reponse = {
        "controles": [
            {"critere": "C1 — La page d'accueil existe", "commande": "test -f accueil.md"},
            {"critere": "C2 — Les tests du projet passent", "verification": "V1"},
            {"critere": "C3 — Le README dit comment lancer", "verification": "V2"},
            {"critere": "C4 — L'application répond vite", "commande": "true"},
        ]
    }
    joueur, provider = _Joueur(), _Recetteur(reponse)

    recette = _recetter(provider, joueur, tmp_path, anterieurs)

    accueil, tests, readme, latence = recette.constats
    # Seuls se rejouent les critères qu'aucune vérification ne tenait sur cet arbre.
    assert [commande for commande, _ in joueur.joues] == ["test -f accueil.md", "true"]
    assert (tests.etat, tests.commande, tests.code) == (
        CONSTAT_TENU,
        "pytest tests/test_module.py",
        0,
    )
    assert tests.repris_de == "dev-python"
    assert tests.preuve.startswith(
        "repris sans être rejoué — la vérification de la tâche « Le correctif et ses tests » "
        "(livraison n° 3) l'a tenu sur un arbre identique au livrable intégré"
    )
    assert "1448 passed" in tests.preuve
    assert (readme.etat, readme.repris_de) == (CONSTAT_TENU, "dev-python")
    assert accueil.repris_de == "" and latence.repris_de == ""
    assert recette.statut == STATUT_RECETTE_TENUE
    assert recette.resume() == (
        "4/4 critère(s) du brief tenu(s), dont 2 repris de la vérification des tâches "
        "sans être rejoués"
    )
    # Le vérificateur a lu ce qui était déjà tenu, et sur quel arbre.
    prompt = provider.prompts[0]
    assert (
        "V1 — tâche « Le correctif et ses tests » (livraison n° 3), sur un arbre identique "
        "au livrable intégré : [tenu] « la suite du module passe » — "
        "`pytest tests/test_module.py` → code 0"
    ) in prompt
    assert '"verification": "V1"' in prompt
    # Repris, il est dit tel dans le projet comme dans le récit.
    texte = recette.en_markdown(objectif="o", run_id="r", horodatage="h")
    assert (
        "- [x] **C2** — Les tests du projet passent — tenu, repris de la vérification de la "
        "tâche dev-python"
    ) in texte
    assert "C2 [tenu, repris de la vérification de la tâche dev-python]" in recette_en_texte(
        recette.to_dict()
    )
    # Et le repris traverse le journal.
    assert RecetteDuRun.depuis(json.loads(json.dumps(recette.to_dict()))) == recette


def test_ce_qu_une_verification_a_tenu_sur_un_autre_arbre_se_rejoue_et_l_ecart_se_nomme(
    tmp_path: Path,
) -> None:
    """La fusion a changé l'arbre : la recette ne reprend rien, elle rejoue, et quand son
    verdict sépare d'une vérification, elle nomme ce qui les sépare — l'arbre, le contrôle."""
    anterieurs = (
        _anterieur("V1", "la page existe", commande="test -f accueil.md", meme_arbre=False),
        _anterieur("V2", "les tests passent", commande="npm test -- accueil", meme_arbre=False),
    )
    reponse = {
        "controles": [
            # Renvoyé à un constat que Maestro ne reprend pas, sans contrôle : rien n'est tenu.
            {"critere": "C1 — La page d'accueil existe", "verification": "V1"},
            {
                "critere": "C2 — Les tests du projet passent",
                "commande": "npm test",
                "verification": "V2",
            },
        ]
    }
    joueur = _Joueur()

    recette = _recetter(_Recetteur(reponse), joueur, tmp_path, anterieurs, CRITERES[:2])

    accueil, tests = recette.constats
    assert [commande for commande, _ in joueur.joues] == ["npm test"]
    assert accueil.etat == CONSTAT_NON_JOUE and accueil.repris_de == ""
    assert "V1" in accueil.preuve
    assert "un arbre qui n'est plus celui du livrable intégré" in accueil.preuve
    assert tests.etat == CONSTAT_NON_TENU
    assert _ECHEC_DES_TESTS.splitlines()[-1] in tests.preuve
    assert (
        "Ce qui la sépare de la vérification de la tâche « Le correctif et ses tests » "
        "(livraison n° 3), qui l'avait tenu par `npm test -- accueil` : l'arbre — le livrable "
        "intégré n'est plus celui qu'elle a jugé ; le contrôle — la recette a joué `npm test`."
    ) in tests.preuve


def test_un_critere_qu_aucune_verification_ne_constatait_se_dit_juge_par_la_seule_recette(
    tmp_path: Path,
) -> None:
    anterieurs = (_anterieur("V1", "la page existe", commande="test -f accueil.md"),)
    reponse = {
        "controles": [
            {"critere": "C1 — La page d'accueil existe", "verification": "V1"},
            {"critere": "C2 — Les tests du projet passent", "commande": "npm test"},
        ]
    }

    recette = _recetter(_Recetteur(reponse), _Joueur(), tmp_path, anterieurs, CRITERES[:2])

    accueil, tests = recette.constats
    assert accueil.repris_de == "dev-python"
    assert tests.etat == CONSTAT_NON_TENU
    assert tests.preuve.endswith(
        "Aucune vérification de tâche ne constatait ce critère : la recette est seule à l'avoir "
        "jugé."
    )


def test_sans_verification_anterieure_le_prompt_de_la_recette_ne_change_pas(
    tmp_path: Path,
) -> None:
    provider = _Recetteur(CONTROLES_DE_RECETTE)

    _recetter(provider, _Joueur(), tmp_path, ())

    assert "<verifications>" not in provider.prompts[0]
    assert '"verification"' not in provider.prompts[0]


def test_l_empreinte_d_un_projet_non_versionne_suit_ce_qu_il_porte(tmp_path: Path) -> None:
    """Deux relevés du même arbre se confondent ; un fichier qui change les sépare. Le dossier
    de Maestro dans le projet n'y entre pas : il n'est pas le livrable."""
    _depot, projet = _projet_nu(tmp_path)
    racine = Path(projet.root)

    avant = empreinte_du_livrable(racine, projet)
    (racine / ".maestro" / "atelier").mkdir(parents=True)
    (racine / ".maestro" / "atelier" / "brouillon.md").write_text("x", encoding="utf-8")

    assert avant and empreinte_du_livrable(racine, projet) == avant
    (racine / "README.md").write_text("# Herbier\n\nnpm run dev\n", encoding="utf-8")
    assert empreinte_du_livrable(racine, projet) != avant
    assert empreinte_du_livrable(racine, None) == ""


def test_l_empreinte_d_une_copie_de_travail_est_celle_de_la_racine_apres_sa_fusion(
    projet_versionne: Project, tmp_path: Path
) -> None:
    """La vérification juge la copie de travail, la recette la racine après la fusion : sur une
    base qui n'a pas bougé, c'est le même arbre, commité ou non, et l'empreinte le dit."""
    racine = Path(projet_versionne.root)
    copie = tmp_path / "copie"
    assert _git(racine, "worktree", "add", "-q", "-b", "maestro/t", str(copie)).returncode == 0
    (copie / "accueil.md").write_text("Bienvenue\n", encoding="utf-8")

    livree = empreinte_du_livrable(copie, projet_versionne)
    assert livree and livree != empreinte_du_livrable(racine, projet_versionne)

    assert _git(copie, "add", "-A").returncode == 0
    assert _git(copie, "commit", "-q", "-m", "accueil").returncode == 0
    assert _git(racine, "merge", "-q", "--ff-only", "maestro/t").returncode == 0
    (racine / ".maestro").mkdir()
    (racine / ".maestro" / "etat.json").write_text("{}", encoding="utf-8")

    assert empreinte_du_livrable(racine, projet_versionne) == livree


def test_sur_un_run_d_une_tache_la_recette_concorde_avec_sa_derniere_verification(
    tmp_path: Path,
) -> None:
    """Critère 3 de #1680, sur le chemin d'un vrai run : la tâche est vérifiée, son verdict est
    consigné avec l'arbre qu'il a jugé, et la recette reprend ce qu'elle a tenu sur ce même
    arbre — sans rien rejouer, et avec le même verdict."""
    depot, projet = _projet_nu(tmp_path)
    verificateur, joueur = _VerificateurQuiReprend(), _Joueur()
    executeur = LocalExecutor(
        _Agent(),
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(_Agent(), DEVELOPER_PROFILE)},
        projets=depot,
        guardrails=Guardrails(validateur=_Validateur()),
        verificateur=VerificateurTaches(
            verificateur, modele="double", joueur=joueur, interprete=_INTERPRETE
        ),
    )
    moteur = OrchestrationEngine(
        _Agent(),
        Orchestrator(_Cadrage(_plan("accueil")), model="chef-de-projet"),
        executor=executeur,
    )
    journal = RunJournal(run_id="run-1680")

    rapport = asyncio.run(
        moteur.run("Un herbier", journal=journal, projet_id=projet.id, mode_brief=MODE_BRIEF_AUTO)
    )

    assert all(r.ok for r in rapport.resultats), [r.erreur for r in rapport.resultats]
    (verification,) = [r for r in journal.records if r.etape == "accueil:verification"]
    assert verification.verification is not None
    assert verification.verification["fingerprint"]
    # La recette a lu la vérification de la tâche, sur le même arbre, et n'a rien rejoué.
    assert (
        "V1 — tâche « Tâche accueil » (livraison n° 1), sur un arbre identique au livrable "
        "intégré : [tenu] « le livrable existe » — `ls` → code 0"
    ) in verificateur.recettes[0]
    assert [commande for commande, _ in joueur.joues] == ["ls"]
    recette = rapport.recette
    assert recette is not None
    assert recette.statut == STATUT_RECETTE_TENUE
    assert all(c.repris_de == "accueil" for c in recette.constats)
    assert recette.resume() == (
        "4/4 critère(s) du brief tenu(s), dont 4 repris de la vérification des tâches "
        "sans être rejoués"
    )


class _VerificateurQuiReprend(_Verificateur):
    """À la recette, renvoie chaque critère du brief au constat V1 de la tâche."""

    async def generate(self, prompt, *, model, system_prompt=None):
        if "Recette du run" in prompt and "<non_tenues>" not in prompt:
            self.recettes.append(prompt)
            return json.dumps(
                {
                    "controles": [
                        {"critere": f"C{rang} — {critere}", "verification": "V1"}
                        for rang, critere in enumerate(CRITERES, start=1)
                    ]
                },
                ensure_ascii=False,
            )
        return await super().generate(prompt, model=model, system_prompt=system_prompt)


# --------------------------------------------------------------------------- #
# ① La recette s'écrit dans le projet, au régime du projet
# --------------------------------------------------------------------------- #


def test_dans_un_projet_non_versionne_la_recette_s_ecrit_en_place(tmp_path: Path) -> None:
    _depot, projet = _projet_nu(tmp_path)

    fichier, raison = ecrire_dans_le_projet(projet, "run-1530", "# Recette\n", accorde=False)

    attendu = Path(projet.root) / ".maestro" / "recette" / "run-1530.md"
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
def projet_versionne(tmp_path: Path) -> Project:
    if shutil.which("git") is None:  # pragma: no cover — la CI porte git (#333)
        pytest.skip("git absent du poste")
    racine = tmp_path / "projets" / "herbier-git"
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# Herbier\n", encoding="utf-8")
    assert _git(racine, "init", "-q", "-b", "main").returncode == 0
    assert _git(racine, "add", "-A").returncode == 0
    assert _git(racine, "commit", "-q", "-m", "init").returncode == 0
    projet = ProjectStore(tmp_path / "depot").create("herbier-git", racine)
    assert projet.versioned, projet
    return projet


def test_dans_un_projet_versionne_la_recette_est_commitee_sous_l_accord_du_run(
    projet_versionne: Project,
) -> None:
    """Commitée seule sur la branche de travail : la racine reste propre, et la fusion
    du run suivant n'est pas refusée pour une racine « occupée »."""
    racine = Path(projet_versionne.root)

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
    projet_versionne: Project,
) -> None:
    racine = Path(projet_versionne.root)

    fichier, raison = ecrire_dans_le_projet(
        projet_versionne, "run-1530", "# Recette\n", accorde=False
    )

    assert fichier == ""
    assert "aucun accord d'écriture" in raison
    assert not (racine / ".maestro").exists()
    assert _git(racine, "status", "--porcelain").stdout == ""


def test_une_racine_occupee_n_est_pas_commitee_sous_les_pieds_de_la_personne(
    projet_versionne: Project,
) -> None:
    racine = Path(projet_versionne.root)
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
    racine = Path(projet.root)
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
    etape = next(r for r in journal.records if r.etape == STEP_ACCEPTANCE)
    assert etape.statut == STATUT_RECETTE_NON_TENUE
    assert etape.sortie == recette.resume()
    assert etape.verification == recette.to_dict()
    assert etape.usage.duration_ms is not None
    grand_livre = RunCost.from_journal(journal)
    assert grand_livre.acceptance.cost_usd == pytest.approx(0.30)
    assert grand_livre.acceptance.calls == 2
    assert all(t.task_id != STEP_ACCEPTANCE for t in grand_livre.tasks)
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
    assert all(r.etape != STEP_ACCEPTANCE for r in journal.records)
    assert not (Path(projet.root) / ".maestro" / "recette").exists()


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
    assert RunCost.from_journal(journal).acceptance.cost_usd is None


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
        etape=STEP_ACCEPTANCE,
        nom="Recette du run — les critères du brief",
        agent="orchestrateur",
        role="Orchestrateur",
        statut=recette.statut,
        entree="2 critère(s)",
        sortie=recette.resume(),
        usage=StepUsage(calls=1, cost_usd=0.3, duration_ms=105_000),
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
    assert execution_.cout.acceptance.cost_usd == pytest.approx(0.3)
    assert execution_.cout.planning.cost_usd is None
    # Une étape du run, jamais une carte : aucune tâche fantôme « recette ».
    assert execution_.cout.tasks == ()
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


def test_la_consigne_du_recit_ne_dit_jamais_une_abstention_comme_un_echec() -> None:
    """#1680 : le message de fin donnait 3/6 sur trois exécutions manquées."""
    consigne = " ".join(SYSTEME_RECIT.split())
    assert "Un critère non vérifiable n'est pas un critère non tenu" in consigne
    assert "ni tenu ni manqué" in consigne


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

    # Le point que la relecture désigne est bien celui qui l'interdit : l'outillage du
    # projet (#1507) s'est inséré avant lui, et la numérotation a suivi.
    assert "8. **Aucune tâche pour vérifier.**" in texte
    assert "9. **Relis ton plan**" in texte
    assert "Relis-le aussi contre le point 8" in texte
    assert "se **retire** du plan" in texte
    assert (
        "Aucune tâche n'a pour seul objet de vérifier, recetter ou valider ce que d'autres "
        "livrent"
    ) in texte
