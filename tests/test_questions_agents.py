"""La question libre d'un agent : la suspension, sa borne, et ce qu'elle n'autorise pas (#1027).

Première moitié du lot final « tests + doc » du chantier #1019 (régime :
[docs/04 §1.2bis](../docs/04-specifications-agents.md)) : les lots #1023 (le
canal dans le moteur) et #1025 (la question dans le fil) ont
différé leurs tests ici. La seconde moitié — ce qu'un agent tranche **seul** et la
lecture qu'on en fait — vit dans `tests/test_decisions_autonomes.py`, parce que
c'est l'autre moitié du régime de docs/37 §2.2 et qu'elle ne partage avec
celle-ci ni ses doubles ni ses questions.

Cinq blocs, un par promesse, et chacun éprouve **la moitié qui ne se voit pas** :

① **la suspension et sa borne.** Le chemin nominal — un agent demande, quelqu'un
   répond — ne dit rien de ce qui compte : qu'il soit *réellement* suspendu tant
   que personne n'a répondu (sans quoi le verbe serait une déclaration de plus),
   et qu'il reparte **quand même** à la borne. Les deux sont pris ensemble sur le
   même double, parce qu'aucune ne vaut sans l'autre : « il attend » sans « il
   repart » décrirait une tâche qui meurt sur un silence, et « il repart » sans
   « il attend » décrirait un verbe qui n'a jamais rien demandé ;

② **l'hypothèse écrite à l'échéance.** C'est le troisième critère de #1023 et la
   seule chose qui distingue « il a tranché seul » de « on ne sait pas ». Elle
   est prise sur les **deux champs** que #1026 sépare — `sortie` porte
   l'hypothèse nue, `description` le motif, borne comprise —, parce que c'est ce
   partage-là qui permet à la liste des décisions d'un run de rendre une
   hypothèse sans rien redécouper ;

③ **la réponse tardive.** La demande reste en vol quand l'agent est reparti, et
   le même appel rejoué retrouve la réponse **sans nouvelle demande**. Le compte
   de demandes est l'observable : sans lui, « il l'a retrouvée » ne se
   distinguerait pas de « il l'a redemandée et quelqu'un a répondu vite » ;

④ **une question ne contourne jamais une validation** (EF-08, second critère du
   parent). Le test joue les deux canaux dans **un même run** : l'agent pose une
   question, une personne lui répond « oui, vas-y », et l'acte classé `ask` qu'il
   commet ensuite reste **refusé** faute de validateur. Les deux moitiés comptent
   l'une pour l'autre — prouver qu'un acte est refusé sans rien demander ne dit
   pas qu'une réponse ne l'autorise pas ;

⑤ **le transport.** `ArbitreQuestionControlTower` et les deux routes du §6.17 :
   l'identité est la **question** et non la tâche, une réponse vide est refusée,
   une question déjà répondue ne se répond pas deux fois, et un bus qui se
   referme **lève** — ce que l'exécuteur traduit en reprise sur hypothèse, jamais
   en échec de tâche.

Aucun appel réseau, aucun SDK, aucun quota : plans constants, fournisseurs
factices, bus mémoire. Le harnais est celui de `tests/test_surface_ecriture_agents.py`
— le lot final du chantier voisin, sur la même surface — plutôt qu'un second à
tenir d'accord.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.agents.permissions import PermissionStore, Verdict
from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    create_app,
)
from maestro.controltower.events import (
    EVENEMENT_QUESTION_DEMANDE,
    EVENEMENT_QUESTION_REPONSE,
)
from maestro.controltower.question import (
    ArbitreQuestionControlTower,
    evenement_question,
)
from maestro.controltower.state import QUESTION_EN_ATTENTE, QUESTION_REPONDUE
from maestro.deliberation import cle_acte
from maestro.engine import OrchestrationEngine
from maestro.engine.executor import (
    STATUT_QUESTION_REPONDUE,
    STATUT_QUESTION_SANS_REPONSE,
    SUFFIXE_ETAPE_QUESTION,
)
from maestro.engine.guardrails import Guardrails
from maestro.engine.questions import (
    VERBE_QUESTION,
    DemandeQuestion,
    identifiant_question,
)
from maestro.orchestrator import Orchestrator
from maestro.providers import question as question_mod
from maestro.providers.arbitrage import NOM_SERVEUR, BornesArbitrage, motif_refus
from maestro.providers.base import ModelProvider
from maestro.providers.claude import _outil_question, _outils_maestro
from maestro.telemetry import RunJournal

# --- Harnais ----------------------------------------------------------------------------

#: La borne de cette suite : ce qu'on laisse à qui répond avant que l'agent ne
#: reparte. Courte, parce qu'aucun test ici ne mesure une durée — ils mesurent ce
#: qui est **écrit** de chaque côté de la borne —, et large devant un tour de
#: boucle : ces tests tournent en parallèle du reste de la suite (`-n auto` en
#: CI), et un écart serré ne mesurerait plus le code mais l'ordonnancement.
BORNE_S = 0.2

QUESTION = "Postgres ou SQLite pour la démo ?"
HYPOTHESE = "je pars sur SQLite, plus simple à embarquer"
CHOIX = ("Postgres", "SQLite")
REPONSE = "Postgres — la démo tourne déjà sur le compose"
#: Ce qu'une personne répond à un agent qui demande s'il peut agir — et qui
#: n'autorise **rien** (bloc ④). Le texte dit oui aussi clairement qu'un texte
#: le peut : s'il devait autoriser quelque chose, c'est ici que ça se verrait.
ACCORD = "oui, vas-y"


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (planificateur factice)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class _Executant(ModelProvider):
    """Exécutant outillé factice : rend son livrable, sans toucher à aucun canal.

    Le double **de base** déclare le protocole en toutes lettres (`on_question`,
    `on_decision`…) plutôt que par `**kwargs` : c'est ce qui le fait rougir le
    jour où le protocole s'élargit, au lieu de lui laisser ignorer en silence un
    canal qu'on vient d'ouvrir.
    """

    name = "executant"

    def __init__(self) -> None:
        self.appels: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None,
        on_decision=None, credit_arbitrage=None, on_courrier=None, on_question=None,
        on_processus=None,
        plafond_tours=None, projet=None, effort=None,
    ):
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        self.appels.append(prompt)
        return f"OUTILLE #{len(self.appels)}"


class PoseUneQuestion(_Executant):
    """Exécutant qui pose **une** question sur sa première tâche, et lit la réponse.

    Il observe au passage ce qu'aucune assertion d'après-coup ne pourrait dire :
    l'appel a-t-il rendu la main **alors que la demande était arrivée chez la
    personne et que personne n'avait encore répondu** ? Le drapeau est posé à cet
    instant précis — la seule seconde où « il attend » se distingue de « il a
    consigné et poursuivi ».
    """

    name = "pose-une-question"

    def __init__(self, arbitre, *, repond: bool = False) -> None:
        super().__init__()
        self._arbitre = arbitre
        # « La personne » répond-elle, une fois la suspension constatée ? Non :
        # personne ne lit, et c'est la borne qui tranche.
        self._repond = repond
        self.suspendu_avant_reponse: bool | None = None
        self.lu: list[str | None] = []

    async def run_agent(self, prompt, **kw):
        if kw.get("on_question") is not None and not self.lu:
            appel = asyncio.ensure_future(
                kw["on_question"](QUESTION, CHOIX, HYPOTHESE)
            )
            await self._arbitre.saisi()
            self.suspendu_avant_reponse = not appel.done()
            if self._repond:
                self._arbitre.repondre()
            self.lu.append(await appel)
        return await super().run_agent(prompt, **kw)


class ReposeLaMemeQuestion(_Executant):
    """Exécutant qui pose **deux fois** la même question sur sa première tâche (#584).

    Le patron que `maestro.providers.question.SANS_REPONSE` décrit à l'agent :
    personne n'a répondu, il est reparti, et il repose plus tard la même question
    — qui doit retrouver la réponse arrivée entre-temps **sans rouvrir
    d'attente**.
    """

    name = "repose-la-meme-question"

    def __init__(self, entre_les_deux) -> None:
        super().__init__()
        self._entre_les_deux = entre_les_deux
        self.lu: list[str | None] = []

    async def run_agent(self, prompt, **kw):
        if kw.get("on_question") is not None and not self.lu:
            self.lu.append(await kw["on_question"](QUESTION, CHOIX, HYPOTHESE))
            await self._entre_les_deux()
            self.lu.append(await kw["on_question"](QUESTION, CHOIX, HYPOTHESE))
        return await super().run_agent(prompt, **kw)


class DemandeEtAgit(_Executant):
    """Exécutant qui pose une question, **puis** commet un acte classé `ask` (EF-08).

    Le double du bloc ④, et l'ordre est tout : la réponse humaine arrive
    **avant** l'acte. Si répondre autorisait quoi que ce soit, c'est ici que ça
    se verrait.
    """

    name = "demande-et-agit"

    ARGUMENTS = {"command": "rm -rf /srv/donnees"}

    def __init__(self, arbitre) -> None:
        super().__init__()
        self._arbitre = arbitre
        self.lu: list[str | None] = []
        self.arbitrages: list[tuple[bool, str]] = []

    async def run_agent(self, prompt, **kw):
        if kw.get("on_question") is not None and not self.lu:
            appel = asyncio.ensure_future(
                kw["on_question"]("Puis-je vider /srv/donnees ?", (), "je n'y touche pas")
            )
            await self._arbitre.saisi()
            self._arbitre.repondre(ACCORD)
            self.lu.append(await appel)
        politique = kw.get("politique")
        decision = None if politique is None else politique.decide("Bash")
        if decision is not None and decision.verdict is Verdict.ARBITRAGE:
            issue = await kw["on_arbitrage_acte"](
                "Bash", dict(self.ARGUMENTS), decision.motif
            )
            self.arbitrages.append(issue)
            approuve, detail = issue
            if not approuve and kw.get("on_refus") is not None:
                kw["on_refus"]("Bash", motif_refus("Bash", detail))
        return await super().run_agent(prompt, **kw)


class _Arbitre:
    """Ce que tout canal factice de cette suite sait faire : être **saisi**.

    `saisi()` rend la main quand la demande est arrivée jusqu'ici, et c'est la
    seule synchronisation dont ces tests ont besoin : un `sleep` fixe mesurerait
    l'ordonnancement de la machine plutôt que le code (docs/10 §8.4bis), et un
    seul tour de boucle ne suffit pas — entre l'appel de l'agent et l'arrivée de
    la demande il y a `wait_for`, la mémoire de délibération et la tâche qu'elle
    crée.
    """

    def __init__(self) -> None:
        self.demandes: list[DemandeQuestion] = []

    async def saisi(self, tours: int = 1000) -> None:
        """Attend que la demande soit parvenue au canal, sans jamais boucler sans fin."""
        for _ in range(tours):
            if self.demandes:
                return
            await asyncio.sleep(0)
        raise AssertionError("la question n'est jamais arrivée au canal")


class ArbitreEnregistreur(_Arbitre):
    """Le canal humain des questions : garde ce qu'on lui a demandé, répond sur ordre.

    L'attente est **indéfinie ici**, comme chez le vrai
    (`ArbitreQuestionControlTower`) : c'est l'exécuteur qui renonce à sa borne.
    Un arbitre qui bornerait de son côté ferait reprendre l'agent sans que rien
    ne l'écrive — exactement ce que le troisième critère interdit.
    """

    def __init__(self) -> None:
        super().__init__()
        self._reponses: list[asyncio.Future[str]] = []

    async def __call__(self, demande: DemandeQuestion) -> str:
        attente: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._reponses.append(attente)
        self.demandes.append(demande)
        return await attente

    def repondre(self, texte: str = REPONSE) -> None:
        """Ce que fait la personne qui lit : elle écrit, et l'agent reprend."""
        for attente in self._reponses:
            if not attente.done():
                attente.set_result(texte)
                return
        raise AssertionError("aucune question en attente à répondre")


class ArbitreMuet(_Arbitre):
    """Personne ne lit. L'attente ne finit jamais : c'est la borne qui tranche."""

    async def __call__(self, demande: DemandeQuestion) -> str:
        self.demandes.append(demande)
        await asyncio.Event().wait()
        raise AssertionError("inatteignable")  # pragma: no cover


def _tache(id_: str, titre: str, dependances=()) -> dict:
    """Une tâche de plan, routée vers le développeur (« backend »)."""
    return {
        "id": id_,
        "titre": titre,
        "description": f"Travail de la tâche « {titre} ».",
        "competences_requises": ["backend"],
        "format_sortie": "Texte",
        "dependances": list(dependances),
    }


#: Deux tâches en chaîne : la première pose la question, la seconde prouve que le
#: run continue — une question, répondue ou non, n'a jamais condamné un aval.
PLAN = json.dumps(
    [
        _tache("schema", "Rédiger le schéma de données"),
        _tache("api", "Exposer l'API", dependances=["schema"]),
    ],
    ensure_ascii=False,
)

OBJECTIF = "Livrer la démo de l'application de notes"


def _moteur(provider, arbitre, *, borne_s=BORNE_S, guardrails=None, permissions=None):
    """Boucle branchée sur le plan constant et sur un canal de question donné."""
    orchestrator = Orchestrator(ConstantProvider(PLAN), model="claude-opus-4-8")
    return OrchestrationEngine(
        provider,
        orchestrator,
        guardrails=guardrails,
        permissions=permissions,
        questionneur=arbitre,
        bornes_question=BornesArbitrage(attente_s=borne_s),
    )


def _run(provider, arbitre, *, run_id="run-1027", **kw):
    """Joue un run complet et rend `(rapport, journal)`."""
    journal = RunJournal(run_id=run_id)
    rapport = asyncio.run(
        _moteur(provider, arbitre, **kw).run(OBJECTIF, journal=journal)
    )
    return rapport, journal


def _questions(journal: RunJournal):
    """Les étapes `<tache>:question` du journal, dans l'ordre d'écriture."""
    return [r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_QUESTION)]


def _appelle_outil(outil, **arguments) -> str:
    """Appelle un outil MCP comme le ferait le SDK, et rend le texte servi à l'agent."""
    reponse = asyncio.run(outil.handler(dict(arguments)))
    return reponse["content"][0]["text"]


def _demande(**champs) -> DemandeQuestion:
    """Une `DemandeQuestion` telle que l'exécuteur la compose, champs fermés compris."""
    defauts = {
        "question_id": "t2:9f1c0a4bd3",
        "question": QUESTION,
        "hypothese": HYPOTHESE,
        "choix": CHOIX,
        "tache_id": "t2",
        "titre": "Rédiger le schéma de données",
        "agent": "bdd",
        "role": "Base de données",
        "run_id": "run-1027",
        "projet_id": "prj-demo",
        "attente_s": 240.0,
    }
    return DemandeQuestion(**{**defauts, **champs})


# --- ① La suspension, et sa borne -------------------------------------------------------


def test_l_agent_est_suspendu_jusqu_a_la_reponse_puis_la_lit_telle_quelle():
    """Le premier critère du parent, par ce qui le distingue d'une déclaration.

    Un verbe qui consignerait sans attendre rendrait la main au premier tour de
    boucle : le drapeau `suspendu_avant_reponse` est là pour dire que ce n'est
    pas le cas, et il est posé par le double **avant** que quiconque ait
    répondu. Sans lui, un `on_question` qui rendrait `None` sur-le-champ
    passerait ce test avec la même trace au journal.

    La réponse est rendue **telle qu'elle a été écrite** : c'est la seule chose
    que ce canal transporte, et la résumer ou la reformater ferait lire à l'agent
    autre chose que ce qu'on lui a répondu.
    """
    arbitre = ArbitreEnregistreur()
    provider = PoseUneQuestion(arbitre, repond=True)

    rapport, journal = _run(provider, arbitre)

    # L'appel n'avait pas rendu la main tant que personne n'avait répondu.
    assert provider.suspendu_avant_reponse is True
    assert provider.lu == [REPONSE]

    # Une seule demande a été portée à la personne, et elle porte ce que l'agent
    # a écrit — plus les champs que l'exécuteur **ferme** (#582, #720) : un agent
    # ne signe pas d'un autre nom et ne rattache pas sa question à la tâche d'un
    # tiers.
    (demande,) = arbitre.demandes
    assert demande.question == QUESTION
    assert demande.choix == CHOIX
    assert demande.hypothese == HYPOTHESE
    assert demande.tache_id == "schema"
    assert demande.titre == "Rédiger le schéma de données"
    assert demande.agent and demande.role
    assert demande.run_id == "run-1027"
    # La borne voyage avec la demande : « il reste quatre minutes » et « il reste
    # une heure » n'appellent pas le même geste.
    assert demande.attente_s == BORNE_S

    # Et le run s'est déroulé normalement — demander n'échoue rien.
    assert [r.ok for r in rapport.resultats] == [True, True]
    assert len(_questions(journal)) == 1


def test_sans_reponse_l_agent_repart_a_la_borne_et_la_tache_aboutit():
    """L'autre moitié : personne ne lit, et l'agent **repart** au lieu de mourir.

    `None` n'est pas un refus et ne doit pas se lire comme tel : personne n'a dit
    non, personne n'a lu. Ce que le canal rend à la borne est donc l'absence de
    réponse, et c'est le fournisseur qui la traduit pour l'agent (bloc ① bis).

    La tâche **aboutit**, et son aval avec elle : une question sans réponse n'a
    jamais été un motif de condamner un travail en cours. C'est la moitié
    invisible du verbe — rien n'échouerait si elle cédait, le run perdrait
    seulement une tâche et tout ce qui en dépend.
    """
    arbitre = ArbitreMuet()
    provider = PoseUneQuestion(arbitre)

    rapport, journal = _run(provider, arbitre)

    assert provider.suspendu_avant_reponse is True
    assert provider.lu == [None]
    assert len(arbitre.demandes) == 1
    assert [r.task_id for r in rapport.resultats] == ["schema", "api"]
    assert [r.ok for r in rapport.resultats] == [True, True]


# --- ② L'hypothèse, écrite à l'échéance -------------------------------------------------


def test_l_hypothese_est_ecrite_au_journal_a_la_borne_dans_deux_champs():
    """Le troisième critère de #1023, sur la forme que #1026 en attend.

    Ce qui est en jeu n'est pas qu'une ligne existe, mais qu'elle soit
    **relisible sans rien redécouper** : `sortie` porte l'hypothèse **nue**,
    `description` le motif — la borne et la question. Les fondre en une phrase
    obligerait la liste des décisions d'un run à deviner par la forme ce que le
    journal savait à l'écriture (docs/05 §6.17, §6.18).

    L'`entree`, elle, porte la question **telle que l'agent l'a écrite**, choix
    et hypothèse compris : sans eux, relire la trace ne permettrait pas de juger
    la réponse.
    """
    arbitre = ArbitreMuet()

    _, journal = _run(PoseUneQuestion(arbitre), arbitre)

    (etape,) = _questions(journal)
    assert etape.etape == f"schema{SUFFIXE_ETAPE_QUESTION}"
    assert etape.statut == STATUT_QUESTION_SANS_REPONSE

    # L'issue, nue : c'est ce que la vue des décisions rendra en première ligne.
    assert etape.sortie == HYPOTHESE

    # Le motif, avec la borne — c'est ici qu'elle est réglée, donc ici qu'on la
    # dit (l'agent, lui, ne la lit pas : cf. `question.SANS_REPONSE`).
    assert QUESTION in etape.description
    assert f"{BORNE_S:g}" in etape.description

    # Ce que l'agent avait écrit, en entier.
    assert QUESTION in etape.entree
    assert HYPOTHESE in etape.entree
    for choix in CHOIX:
        assert choix in etape.entree

    # Demander ne dépense pas : le coût de la tâche est porté par son étape
    # finale, et rendre compte de soi ne doit rien coûter.
    assert etape.usage.cout_usd in (None, 0.0)
    assert etape.run_id == "run-1027"


def test_une_reponse_recue_est_consignee_dans_les_memes_champs():
    """Les deux issues s'écrivent **de la même façon**, parce qu'elles se valent.

    Une réponse humaine est un renseignement reçu, une reprise sur hypothèse est
    une décision prise par l'agent : les deux doivent se lire, et depuis la même
    colonne. Ce qui les sépare est le **statut**, et lui seul — c'est ce qui
    permet à la liste des décisions d'un run de ne retenir que la seconde sans
    juger d'un texte.
    """
    arbitre = ArbitreEnregistreur()

    _, journal = _run(PoseUneQuestion(arbitre, repond=True), arbitre)

    (etape,) = _questions(journal)
    assert etape.statut == STATUT_QUESTION_REPONDUE
    assert etape.sortie == REPONSE
    assert QUESTION in etape.description
    assert etape.usage.cout_usd in (None, 0.0)


def test_une_question_ne_fait_pas_changer_la_tache_de_colonne():
    """Demander n'est pas se prononcer sur le sort de sa tâche (docs/31 §3.4).

    L'agent travaille, il demande un renseignement, et il reprendra quoi qu'il
    arrive. Poser un statut ici condamnerait l'aval par la cascade de #43 — au
    moment précis où l'agent cherche à bien faire.
    """
    arbitre = ArbitreMuet()

    rapport, _ = _run(PoseUneQuestion(arbitre), arbitre)

    assert [r.task_id for r in rapport.resultats] == ["schema", "api"]
    assert all(
        r.statut not in (STATUT_QUESTION_SANS_REPONSE, STATUT_QUESTION_REPONDUE)
        for r in rapport.resultats
    )


# --- ① bis. Le verbe vu de l'outil servi à l'agent ---------------------------------------


def test_l_outil_sert_la_reponse_puis_la_suite_a_donner():
    """Ce que l'agent lit quand on lui a répondu — et ce qu'il doit en faire.

    La seconde moitié compte autant que la première : sans elle, un agent peut
    très bien lire une réponse et la traiter comme une remarque.
    """
    async def canal(texte, choix, hypothese):
        return REPONSE

    servi = _appelle_outil(
        _outil_question(canal), question=QUESTION, hypothese=HYPOTHESE
    )

    assert REPONSE in servi
    assert "compte-rendu" in servi


def test_l_outil_ne_dit_pas_un_refus_quand_personne_n_a_repondu():
    """`None` n'est **pas** un refus, et le texte servi ne doit pas le dire comme tel.

    Trois choses, et les trois comptent : personne n'a répondu, l'agent reprend
    sur **son** hypothèse (recopiée pour qu'il n'ait pas à se souvenir de ce
    qu'il a écrit trois tours plus tôt), et la question reste posée.

    ⚠ La **borne n'y est pas nommée**, et c'est délibéré : elle vit chez
    l'appelant, et la redire ici ferait deux supports pour un même chiffre — pour
    une valeur qui n'apprend rien à un agent dont la seule suite possible est de
    reprendre son travail. C'est le journal qui la porte (bloc ②).
    """
    async def canal(texte, choix, hypothese):
        return None

    servi = _appelle_outil(
        _outil_question(canal), question=QUESTION, hypothese=HYPOTHESE
    )

    assert HYPOTHESE in servi
    assert "refus" not in servi.lower()
    # Le chiffre de la borne n'apparaît nulle part dans ce que l'agent lit.
    assert f"{BORNE_S:g}" not in servi
    assert "240" not in servi


def test_une_question_sans_hypothese_n_est_posee_a_personne():
    """Le seul refus de forme propre à ce verbe, et il n'est pas formel.

    L'attente est **bornée** : une question sans hypothèse promet à l'agent une
    reprise qu'il n'a pas décrite — à la borne, il n'aurait rien à reprendre et
    le journal rien à consigner. La question n'est donc pas posée du tout, et le
    canal n'est **pas appelé** : refuser après coup laisserait la demande partir
    chez quelqu'un pour rien.

    Le témoin compte autant que le refus : prouver qu'une hypothèse vide ne
    demande rien ne dit pas que le verbe demande quoi que ce soit.
    """
    appels: list[tuple[str, tuple[str, ...], str]] = []

    async def canal(texte, choix, hypothese):
        appels.append((texte, choix, hypothese))
        return REPONSE

    outil = _outil_question(canal)

    # ① L'hypothèse manquante : rien n'est demandé.
    servi = _appelle_outil(outil, question=QUESTION, hypothese="   ")
    assert servi == question_mod.HYPOTHESE_MANQUANTE
    assert appels == []

    # ② La question manquante : rien n'est demandé non plus, et le texte ne dit
    # pas un refus — il n'y a qu'un champ à remplir.
    servi = _appelle_outil(outil, question="", hypothese=HYPOTHESE)
    assert servi == question_mod.QUESTION_MANQUANTE
    assert appels == []

    # ③ Le témoin : la même expérience complète demande, elle.
    servi = _appelle_outil(outil, question=QUESTION, hypothese=HYPOTHESE)
    assert REPONSE in servi
    assert appels == [(QUESTION, (), HYPOTHESE)]


def test_un_canal_en_erreur_est_dit_a_l_agent_et_ne_tue_rien():
    """Le cinquième cas de l'outil : le callback a levé (bus refermé, transport mort).

    On le **dit** — l'agent attendait quelque chose, donc l'échec change quelque
    chose pour lui — et on ne laisse surtout pas l'exception remonter : elle
    tuerait la tâche au moment précis où l'agent cherchait à bien faire. Son
    hypothèse lui est recopiée, parce que c'est exactement ce qui lui reste.
    """
    async def canal(texte, choix, hypothese):
        raise RuntimeError("bus refermé")

    servi = _appelle_outil(
        _outil_question(canal), question=QUESTION, hypothese=HYPOTHESE
    )

    assert "bus refermé" in servi
    assert HYPOTHESE in servi


def test_le_verbe_porte_le_nom_sous_lequel_une_politique_le_designe():
    """Le nom complet est un **contrat** (docs/04 §1.4ter), et il n'avait aucun lecteur.

    Le droit de ce verbe est tenu par la couche permissions (#110) : « un outil
    MCP s'appelle `mcp__maestro__<nom>`, donc une politique le cite, l'autorise
    ou le refuse ». Cette phrase n'est vraie que tant que le nom ne bouge pas —
    un renommage de `NOM_OUTIL` emporterait `OUTIL_QUESTION` en silence, et une
    politique écrite sur l'ancien nom cesserait de désigner quoi que ce soit.

    Les deux formes sont écrites : la dérivée (qui garde l'accord entre les
    morceaux) et la littérale (qui garde le nom — c'est elle qu'un fichier de
    politique contient).
    """
    assert question_mod.OUTIL_QUESTION == f"mcp__{NOM_SERVEUR}__{question_mod.NOM_OUTIL}"
    assert question_mod.OUTIL_QUESTION == "mcp__maestro__poser_une_question"


def test_le_verbe_n_est_monte_que_si_un_canal_lui_est_cable():
    """Servir à un agent un outil qui suspend sa tâche **pour personne** serait pire.

    C'est la règle du porte-outils (#718) : les canaux sont indépendants, la
    liste rendue peut être vide, et un verbe sans câblage n'est pas monté du tout
    plutôt que monté sans aboutir.
    """
    async def canal(texte, choix, hypothese):  # pragma: no cover — jamais appelé ici
        return REPONSE

    assert _outils_maestro() == []
    monte = _outils_maestro(on_question=canal)
    assert [outil.name for outil in monte] == [question_mod.NOM_OUTIL]


def test_les_choix_sont_relus_sans_jamais_faire_echouer_la_question():
    """`choix_nettoyes` est une **relecture tolérante**, jamais une validation.

    Ce qui arrive vient d'un modèle, pas d'un appelant : une liste mal formée ne
    doit pas faire perdre la question, qui est la partie obligatoire. Trois
    nettoyages, chacun pour sa raison — un bouton vide n'est pas une option, deux
    fois la même option est une question qu'on ne peut pas trancher, et trente
    options ne sont plus une question mais un formulaire.

    L'ordre de l'agent est **conservé** : c'est le sien, il porte souvent une
    préférence, et le trier serait réécrire sa question.
    """
    # Une chaîne n'est pas une liste de choix — sans cette garde, « SQLite »
    # deviendrait six boutons d'une lettre.
    assert question_mod.choix_nettoyes("SQLite") == ()
    assert question_mod.choix_nettoyes(None) == ()

    # Vides, blancs, doublons et entrées non textuelles écartés ; l'ordre tenu.
    assert question_mod.choix_nettoyes(
        ["SQLite", "  ", "Postgres", "SQLite", 3, None, "  Postgres  "]
    ) == ("SQLite", "Postgres")

    # Le plafond mord, et en silence : la question reste posable.
    assert len(question_mod.choix_nettoyes([f"option {n}" for n in range(30)])) == (
        question_mod.CHOIX_MAX
    )

    # Un « choix » de mille signes est une réponse déguisée en bouton.
    (long,) = question_mod.choix_nettoyes(["x" * 500])
    assert len(long) == question_mod.CHOIX_LONGUEUR_MAX


# --- ③ La réponse tardive ---------------------------------------------------------------


def test_une_reponse_tardive_est_retrouvee_par_le_meme_appel_rejoue():
    """Ce que `SANS_REPONSE` promet à l'agent, tenu de bout en bout (#584).

    L'agent est reparti sur son hypothèse ; la demande, elle, est **restée en
    vol**. La réponse arrive quand plus personne ne l'attend, et le même appel
    rejoué la retrouve — **sans nouvelle demande**.

    Le compte de demandes est l'observable qui rend le test concluant : sans lui,
    « il l'a retrouvée » ne se distinguerait pas de « il a redemandé et quelqu'un
    a répondu vite ». Et les deux étapes de journal disent les deux temps :
    l'hypothèse d'abord, la réponse ensuite.
    """
    arbitre = ArbitreEnregistreur()

    async def entre_les_deux():
        # La personne finit sa lecture après la borne : plus personne n'attend.
        arbitre.repondre()
        await asyncio.sleep(0)

    provider = ReposeLaMemeQuestion(entre_les_deux)
    _, journal = _run(provider, arbitre)

    assert provider.lu == [None, REPONSE]
    # Une seule demande devant la personne, pour deux appels de l'agent.
    assert len(arbitre.demandes) == 1

    # Deux étapes, et elles disent les deux temps dans l'ordre.
    etapes = _questions(journal)
    assert [e.statut for e in etapes] == [
        STATUT_QUESTION_SANS_REPONSE,
        STATUT_QUESTION_REPONDUE,
    ]
    assert [e.sortie for e in etapes] == [HYPOTHESE, REPONSE]


def test_l_identifiant_d_une_question_est_deterministe_et_nomme_sa_tache():
    """Pourquoi la question, et pas la tâche, porte l'identité (docs/05 §6.17).

    La file des validations s'indexe par `tache_id` et l'assume — une nouvelle
    demande remplace la précédente. Ici ce serait faux : une tâche en pose
    plusieurs, et une question laissée sans réponse **reste en vol** pendant que
    l'agent reprend. Deux questions d'une même tâche peuvent donc attendre
    ensemble.

    L'identifiant est **dérivé** de la clé d'acte plutôt que tiré au sort, et
    c'est ce qui rend adressable après coup une réponse arrivée trop tard : la
    même question reposée porte le même identifiant.
    """
    cle = cle_acte(VERBE_QUESTION, {"question": QUESTION, "hypothese": HYPOTHESE})
    autre = cle_acte(VERBE_QUESTION, {"question": "Et le cache ?", "hypothese": HYPOTHESE})

    assert identifiant_question("t2", cle) == identifiant_question("t2", cle)
    assert identifiant_question("t2", cle).startswith("t2:")
    # Deux questions d'une même tâche ne se confondent pas…
    assert identifiant_question("t2", cle) != identifiant_question("t2", autre)
    # … et la même question posée par deux tâches non plus.
    assert identifiant_question("t2", cle) != identifiant_question("t3", cle)


def test_une_question_et_un_acte_ne_peuvent_pas_se_croiser_dans_la_memoire():
    """Les deux canaux partagent **une** mémoire par tâche, et ne s'y rencontrent jamais.

    C'est ce que `VERBE_QUESTION` existe pour tenir : les clés d'un acte portent
    le nom **préfixé** de l'outil intercepté (`mcp__…`, la forme que voit le hook
    `PreToolUse`), celles d'une question le **verbe nu**. Deux espaces de clés qui
    ne peuvent pas se croiser — sans quoi une question retrouverait la décision
    rendue sur un acte, ou l'inverse, et un `True` retenu pour un `rm -rf`
    deviendrait la réponse d'une question ouverte.
    """
    arguments = {"question": QUESTION, "hypothese": HYPOTHESE, "choix": ""}

    assert VERBE_QUESTION == question_mod.NOM_OUTIL
    assert VERBE_QUESTION != question_mod.OUTIL_QUESTION
    assert cle_acte(VERBE_QUESTION, arguments) != cle_acte(
        question_mod.OUTIL_QUESTION, arguments
    )


# --- ④ Répondre n'autorise rien (EF-08) --------------------------------------------------


def test_repondre_a_une_question_n_autorise_aucun_acte(tmp_path):
    """Le second critère du parent #1019, joué **dans un même run**.

    L'agent demande, une personne lui répond « oui, vas-y », puis il commet un
    acte classé `ask`. Sans canal d'arbitrage, cet acte reste **refusé** : le
    texte écrit en réponse ne traverse pas le hook `PreToolUse`, ne compose
    aucune `DemandeValidation`, et n'est jamais lu comme une approbation.

    Les deux moitiés comptent l'une pour l'autre, et c'est pourquoi elles sont
    dans la même expérience : prouver qu'un acte est refusé sans rien demander ne
    dit pas qu'une réponse ne l'autorise pas, et prouver qu'une question reçoit
    sa réponse ne dit pas que l'acte a été jugé à part.
    """
    store = PermissionStore(tmp_path / "permissions")
    store.racine.mkdir(parents=True, exist_ok=True)
    (store.racine / "developpeur.json").write_text(
        json.dumps({"ask": ["Bash"]}, ensure_ascii=False), encoding="utf-8"
    )
    arbitre = ArbitreEnregistreur()
    provider = DemandeEtAgit(arbitre)

    rapport, _ = _run(
        provider,
        arbitre,
        # Aucun validateur : personne ne peut trancher un acte. C'est le régime
        # dans lequel « approuver n'est jamais le défaut sûr » se vérifie.
        guardrails=Guardrails(),
        permissions=store,
    )

    # La prémisse : l'agent a bien eu sa réponse, et elle dit oui.
    assert provider.lu == [ACCORD]

    # Et l'acte est refusé quand même. C'est tout le ticket.
    assert provider.arbitrages
    approuve, detail = provider.arbitrages[0]
    assert approuve is False
    assert detail  # un refus motivé, jamais un silence

    # Le run n'a pas échoué pour autant : l'agent a été refusé, pas tué.
    assert [r.ok for r in rapport.resultats] == [True, True]


# --- ⑤ Le transport : l'arbitre Control Tower et ses deux routes -------------------------


def test_l_evenement_de_demande_porte_la_borne_en_toutes_lettres_et_en_date():
    """Pourquoi la borne voyage **deux fois**, et pourquoi ce n'est pas un doublon.

    `detail` est la **phrase** qu'un fil affiche sans rien composer ; `echeance`
    est le **fait** avec lequel un écran compare son horloge pour dire si l'agent
    est déjà reparti (#1025). Sans la seconde, l'écran devrait lire un chiffre
    dans la phrase — juger du texte par un motif, ce que le dépôt refuse (#746) —
    ou recopier `BornesArbitrage.attente_s` côté navigateur, soit deux supports
    pour un même réglage.

    Et ce qui vient de l'agent est **expurgé des secrets** : question, choix et
    hypothèse sont du texte qu'un modèle a composé, au même titre que la raison
    d'un arbitrage. Les identifiants, eux, sont les nôtres.
    """
    event = evenement_question(
        _demande(hypothese=f"je réutilise le jeton {'ghp_' + 'a' * 36}")
    )

    assert event.type == EVENEMENT_QUESTION_DEMANDE
    assert event.statut == QUESTION_EN_ATTENTE
    assert event.description == QUESTION
    assert event.choix == list(CHOIX)
    assert event.question_id == "t2:9f1c0a4bd3"
    assert event.tache_id == "t2"
    assert event.projet_id == "prj-demo"

    # La borne, dite puis datée — et l'échéance tombe après la demande.
    assert "240" in event.detail
    assert event.echeance > event.horodatage

    # Le secret est parti du texte de l'agent, dans les deux endroits où il vit.
    assert "ghp_" not in event.hypothese
    assert "ghp_" not in event.detail


def test_l_arbitre_publie_puis_attend_la_reponse_de_sa_propre_question():
    """Le filtre est l'**identifiant de la question**, jamais la tâche.

    Une tâche en pose plusieurs, et une question laissée sans réponse reste en
    vol pendant que l'agent reprend : filtrer par `tache_id` ferait rendre à la
    première la réponse écrite pour la seconde. Le test le prouve en envoyant
    d'abord une réponse visant une **autre** question de la même tâche — elle ne
    doit rien débloquer.

    L'abonnement est posé **avant** la publication (même précaution qu'en #48 et
    #320) : une réponse instantanée ne doit pas tomber dans le vide.
    """
    async def scenario():
        bus = InMemoryEventBus()
        demande = _demande()
        arbitre = ArbitreQuestionControlTower(bus)
        attente = asyncio.ensure_future(arbitre(demande))
        await asyncio.sleep(0)

        # Une réponse qui vise une autre question de la même tâche : ignorée.
        await bus.publish(
            Event(
                type=EVENEMENT_QUESTION_REPONSE,
                tache_id=demande.tache_id,
                statut=QUESTION_REPONDUE,
                detail="réponse à côté",
                question_id="t2:0000000000",
            )
        )
        await asyncio.sleep(0)
        encore_en_attente = not attente.done()

        await bus.publish(
            Event(
                type=EVENEMENT_QUESTION_REPONSE,
                tache_id=demande.tache_id,
                statut=QUESTION_REPONDUE,
                detail=REPONSE,
                question_id=demande.question_id,
            )
        )
        return encore_en_attente, await attente

    encore_en_attente, reponse = asyncio.run(scenario())

    assert encore_en_attente is True
    assert reponse == REPONSE


class BusQuiSeReferme(InMemoryEventBus):
    """Un bus dont le flux se **tarit** : personne ne répondra jamais.

    `InMemoryEventBus.close()` est un no-op assumé (seul un bus à connexions a
    quelque chose à libérer), donc il ne peut pas jouer ce cas : c'est la fin de
    l'itération qu'il faut simuler, pas la fermeture d'une ressource. Même
    double que `tests/test_brief.py`, sur l'autre canal d'attente.
    """

    async def subscribe(self):
        return
        yield  # pragma: no cover — fait de `subscribe` un générateur asynchrone


def test_un_bus_referme_leve_au_lieu_de_rendre_une_reponse_par_defaut():
    """Le fail-safe, dans le bon sens — et ce que l'exécuteur en fait.

    Rendre une chaîne vide ferait lire à l'agent « on t'a répondu : rien », ce
    qui est pire que le silence. L'attente **lève** donc, et c'est l'exécuteur
    qui traduit — en reprise sur hypothèse, consignée, jamais en échec de tâche
    (`question.CANAL_EN_ERREUR` le dit à l'agent, bloc ① bis).
    """
    async def scenario():
        arbitre = ArbitreQuestionControlTower(BusQuiSeReferme())
        return await arbitre(_demande())

    with pytest.raises(RuntimeError) as capture:
        asyncio.run(scenario())

    # Le message nomme la question restée sans réponse : c'est ce qu'on lit dans
    # le texte servi à l'agent quand le canal casse.
    assert "t2:9f1c0a4bd3" in str(capture.value)


@pytest.fixture()
def bus():
    """Bus mémoire partagé entre le test (producteur) et l'app (consommatrice)."""
    return InMemoryEventBus()


@pytest.fixture()
def state():
    """Projection d'état injectée : les tests REST la peuplent directement."""
    return ControlTowerState()


@pytest.fixture()
def client(bus, state):
    """TestClient de l'app sur bus mémoire, pompe démarrée (contexte = lifespan)."""
    with TestClient(create_app(bus=bus, state=state)) as client:
        yield client


def _pose(state: ControlTowerState, **champs) -> str:
    """Projette une question posée, comme le ferait le moteur, et rend son identifiant."""
    event = evenement_question(_demande(**champs))
    state.appliquer(event)
    return event.question_id


def test_la_file_des_questions_se_cadre_comme_les_autres_vues(client, state):
    """`projet` est **obligatoire**, au contrat commun du §6.0 (#277).

    Une question appartient au projet de la tâche qui la pose, et une Control
    Tower cadrée sur un projet n'a pas à faire répondre pour un travail qui se
    déroule ailleurs. Omis, le paramètre est **refusé** plutôt que remplacé par
    un mélange silencieux — un refus motivé se diagnostique là où une liste vide
    se confondrait avec « aucune question ».

    Le cadrage sur un projet **déclaré** est éprouvé dans
    `tests/test_appartenance_projet.py`, qui est la suite du contrat et porte le
    dépôt de projets qu'il exige ; ici on garde les deux portées qui n'en
    demandent pas, et la forme servie.
    """
    _pose(state, question_id="t2:aaaa", projet_id="prj-demo")
    _pose(state, question_id="t9:bbbb", projet_id=None, tache_id="t9")

    assert client.get("/api/questions").status_code == 422

    toutes = client.get("/api/questions?projet=tous").json()
    assert [q["question_id"] for q in toutes] == ["t2:aaaa", "t9:bbbb"]

    # La vue qu'aucun paramètre ne permettait d'atteindre : les travaux hors projet.
    hors_projet = client.get("/api/questions?projet=aucun").json()
    assert [q["question_id"] for q in hors_projet] == ["t9:bbbb"]

    # La forme servie est celle du §6.17, jusqu'aux choix rendus en liste.
    (question,) = [q for q in toutes if q["question_id"] == "t2:aaaa"]
    assert question["question"] == QUESTION
    assert question["hypothese"] == HYPOTHESE
    assert question["choix"] == list(CHOIX)
    assert question["statut"] == QUESTION_EN_ATTENTE
    assert question["reponse"] == ""


def test_repondre_met_la_question_a_jour_et_porte_la_reponse_au_moteur(client, bus, state):
    """Le REST répond **déjà à jour**, puis le bus porte la réponse à l'agent.

    Même patron que la décision de validation : l'état est appliqué d'abord (donc
    ce qu'on rend est l'état d'après), la publication ensuite — l'agent, suspendu
    sur ce même bus, la reçoit et reprend. La pompe réapplique l'événement sans
    effet.
    """
    question_id = _pose(state)

    reponse = client.post(
        f"/api/questions/{question_id}/reponse", json={"reponse": f"  {REPONSE}  "}
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["statut"] == QUESTION_REPONDUE
    # Rognée à la frontière, comme partout : la réponse voyage telle qu'écrite,
    # sans les blancs de la saisie.
    assert corps["reponse"] == REPONSE
    assert state.question(question_id).reponse == REPONSE


def test_une_reponse_vide_est_refusee_parce_qu_elle_n_apprend_rien(client, state):
    """`422`, et la raison n'est pas formelle.

    Une réponse vide dirait à l'agent qu'on lui a répondu sans rien lui
    apprendre : il reprendrait sur son hypothèse en croyant le contraire —
    c'est-à-dire pire que le silence, qui lui dit au moins la vérité.
    """
    question_id = _pose(state)

    reponse = client.post(f"/api/questions/{question_id}/reponse", json={"reponse": "   "})

    assert reponse.status_code == 422
    # Rien n'a bougé : la question attend toujours quelqu'un.
    assert state.question(question_id).statut == QUESTION_EN_ATTENTE


def test_une_question_inconnue_ou_deja_repondue_se_distingue(client, state):
    """`404` et `409` disent deux choses, et il faut les deux.

    `404` : aucune question ne porte cet identifiant. `409` : elle a déjà reçu sa
    réponse — jamais deux fois répondu, l'agent n'ayant lu que la première.
    """
    assert client.post("/api/questions/inconnue/reponse", json={"reponse": "x"}).status_code == 404

    question_id = _pose(state)
    assert client.post(
        f"/api/questions/{question_id}/reponse", json={"reponse": REPONSE}
    ).status_code == 200
    seconde = client.post(
        f"/api/questions/{question_id}/reponse", json={"reponse": "et finalement non"}
    )

    assert seconde.status_code == 409
    # La première fait foi : c'est celle que l'agent a lue.
    assert state.question(question_id).reponse == REPONSE


def test_une_question_republiee_ne_reouvre_pas_une_reponse_deja_recue(state):
    """Le journal durable est **rejoué** au démarrage, et rien ne garantit l'ordre.

    La demande arrive avant la réponse dans le flux d'origine, mais une
    rediffusion peut les présenter dans l'autre sens : une question retombée « en
    attente » ferait attendre quelqu'un pour un agent qui a déjà sa réponse.
    Même garde que `_applique_brief_decision` sur l'autre bout de sa chaîne.
    """
    question_id = _pose(state)
    state.appliquer(
        Event(
            type=EVENEMENT_QUESTION_REPONSE,
            statut=QUESTION_REPONDUE,
            detail=REPONSE,
            question_id=question_id,
        )
    )

    # La demande repasse — rediffusion, journal rejoué.
    _pose(state)

    question = state.question(question_id)
    assert question.statut == QUESTION_REPONDUE
    assert question.reponse == REPONSE


def test_une_reponse_a_une_question_inconnue_est_ignoree(state):
    """La projection est un **miroir**, pas une source de vérité.

    Une réponse dont la demande n'a jamais été projetée n'invente pas une
    question : elle ne fabrique pas une ligne que personne ne pourrait relier à
    un agent, une tâche ou un run.
    """
    state.appliquer(
        Event(
            type=EVENEMENT_QUESTION_REPONSE,
            statut=QUESTION_REPONDUE,
            detail=REPONSE,
            question_id="jamais-posee",
        )
    )

    assert state.question("jamais-posee") is None
    assert state.questions() == []
