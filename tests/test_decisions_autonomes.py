"""Ce qu'un agent tranche **seul**, consigné puis relu (#1027, parent #1019).

Seconde moitié du lot final « tests + doc » du chantier : les lots #1024 (le
verbe `consigner_decision`) et #1026 (la lecture dans la vue d'un run) ont
différé leurs tests ici. La première moitié — ce qu'un agent **demande**, et ce
qui se passe sans réponse — vit dans `tests/test_questions_agents.py`.

Le partage entre les deux fichiers est celui du régime lui-même
([docs/37 §2.2](../docs/37-decision-equipe-sur-mesure.md), socle des playbooks) :
*ce qui demande un humain se demande, tout le reste se tranche seul **et se
consigne***. Et la condition que le parent pose à cette autonomie tient en une
phrase — *elle n'est acceptable que si elle se vérifie après coup* —, dont ce
fichier est la garde.

Quatre blocs :

① **le verbe, et ce qu'il refuse.** Une décision sans son **motif** n'est pas
   consignée, et c'est le point où ce verbe est plus exigeant que ses voisins :
   « j'ai retenu SQLite » ne se conteste pas, donc ne se vérifie pas — et
   « vérifiable après coup » est la condition même du chantier. Le refus est pris
   **des deux côtés**, l'outil servi à l'agent et le canal de l'exécuteur, parce
   que ce chemin a deux entrées et que les garder une seule fois laisserait
   l'autre libre de dériver (même règle qu'en #721 pour `signaler_blocage`) ;

② **ce que la trace porte**, et surtout dans **quels champs** : `sortie` la
   décision, `description` le motif. Deux champs et jamais un texte — c'est
   l'engagement de #1024, et le tenir est tout ce que #1026 demandait pour
   n'avoir rien à redécouper. Le reste est fermé par l'exécuteur : un agent ne
   signe pas d'un autre nom et ne rattache pas sa décision à la tâche d'un
   tiers ;

③ **la lecture** (`maestro.controltower.decisions`) : deux familles séparées par
   `origine` et jamais mêlées, une question **répondue** qui n'y entre pas (c'est
   le contraire de l'autonomie), l'ordre du plus récent au plus ancien, et un
   plafond qui **le dit** — `total` et `hypotheses` comptent avant lui ;

④ **la route** `GET /api/executions/{run_id}/decisions`, son 404, et le fait que
   rien n'est créé : chaque ligne garde l'identifiant que
   `GET /api/journal?run_id=…` lui donne, si bien que les deux lectures ne
   peuvent pas se contredire.

Ni Redis, ni réseau, ni appel modèle : l'app est la vraie (`create_app`) sur bus
mémoire, alimentée par l'historique qu'elle rejoue au démarrage — ce que la pompe
lui livre en production. Le harnais de run est celui de
`tests/test_questions_agents.py`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.decisions import (
    ORIGINE_HYPOTHESE,
    ORIGINE_TRANCHEE,
    PLAFOND_DECISIONS,
    decisions_du_run,
)
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_DECISION,
    EVENEMENT_TACHE_STATUT,
)
from maestro.controltower.journal import EntreeJournal
from maestro.controltower.state import EXECUTION_EN_COURS
from maestro.engine import OrchestrationEngine
from maestro.engine.executor import (
    STATUT_DECISION_AUTONOME,
    STATUT_QUESTION_REPONDUE,
    STATUT_QUESTION_SANS_REPONSE,
    SUFFIXE_ETAPE_DECISION,
)
from maestro.orchestrator import Orchestrator
from maestro.providers import decision as decision_mod
from maestro.providers.arbitrage import NOM_SERVEUR
from maestro.providers.base import ModelProvider
from maestro.providers.claude import _outil_decision, _outils_maestro
from maestro.telemetry import RunJournal

# --- Harnais ----------------------------------------------------------------------------

DECISION = "Pagination en curseur plutôt qu'en offset"
RAISON = "la liste est triée par date et l'offset dérive à chaque insertion"

RUN = "run-1027"
PROJET = "prj-demo"


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

    Le double **de base** déclare le protocole en toutes lettres plutôt que par
    `**kwargs` : c'est ce qui le fait rougir le jour où le protocole s'élargit,
    au lieu de lui laisser ignorer en silence un canal qu'on vient d'ouvrir.
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


class ConsigneUneDecision(_Executant):
    """Exécutant qui consigne **une** décision sur sa première tâche (#1024).

    Une seule fois, et sur la première tâche seulement : le compte exact est ce
    qui permet de dire, plus loin, qu'une trace de plus vient bien de ce verbe et
    pas d'un double qui aurait écrit à chaque tour.
    """

    name = "consigne-une-decision"

    def __init__(self, decision: str = DECISION, raison: str = RAISON) -> None:
        super().__init__()
        self.decision = decision
        self.raison = raison
        self.consignees = 0

    async def run_agent(self, prompt, **kw):
        if kw.get("on_decision") is not None and self.consignees == 0:
            self.consignees += 1
            kw["on_decision"](self.decision, self.raison)
        return await super().run_agent(prompt, **kw)


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


#: Deux tâches en chaîne : la première décide, la seconde prouve que le run
#: continue — consigner ce qu'on a tranché n'a jamais condamné un aval.
PLAN = json.dumps(
    [
        _tache("api", "Exposer l'API"),
        _tache("recette", "Recetter l'API", dependances=["api"]),
    ],
    ensure_ascii=False,
)

OBJECTIF = "Livrer l'API de la démo"


def _run(provider, *, run_id=RUN):
    """Joue un run complet et rend `(rapport, journal)`."""
    orchestrator = Orchestrator(ConstantProvider(PLAN), model="claude-opus-4-8")
    journal = RunJournal(run_id=run_id)
    rapport = asyncio.run(
        OrchestrationEngine(provider, orchestrator).run(OBJECTIF, journal=journal)
    )
    return rapport, journal


def _decisions(journal: RunJournal):
    """Les étapes `<tache>:decision` du journal, dans l'ordre d'écriture."""
    return [r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_DECISION)]


def _appelle_outil(outil, **arguments) -> str:
    """Appelle un outil MCP comme le ferait le SDK, et rend le texte servi à l'agent."""
    reponse = asyncio.run(outil.handler(dict(arguments)))
    return reponse["content"][0]["text"]


def _entree(rang: int, type_: str, **champs) -> EntreeJournal:
    """Une entrée de journal, telle que la pompe la consigne."""
    defauts = {
        "run_id": RUN,
        "tache_id": "api",
        "titre": "Décision de l'agent — Exposer l'API",
        "agent": "developpeur",
        "role": "Développeur",
        "horodatage": "2026-09-20T17:04:11+00:00",
        "projet_id": PROJET,
    }
    return EntreeJournal(rang=rang, type=type_, **{**defauts, **champs})


def _tranchee(rang: int, **champs) -> EntreeJournal:
    """Une décision que l'agent a jugée sienne (#1024)."""
    return _entree(
        rang,
        EVENEMENT_TACHE_DECISION,
        statut=STATUT_DECISION_AUTONOME,
        detail=DECISION,
        description=RAISON,
        **champs,
    )


def _hypothese(rang: int, **champs) -> EntreeJournal:
    """Une hypothèse reprise faute de réponse (#1023) — rangée en activité d'agent."""
    return _entree(
        rang,
        EVENEMENT_AGENT_ACTIVITE,
        statut=STATUT_QUESTION_SANS_REPONSE,
        detail="je pars sur SQLite",
        description="aucune réponse après 240 s à : Postgres ou SQLite ?",
        **champs,
    )


# --- ① Le verbe, et ce qu'il refuse ------------------------------------------------------


def test_une_decision_sans_son_motif_n_est_pas_consignee():
    """Le point où ce verbe est plus exigeant que ses voisins — et son témoin.

    Une décision sans motif n'est **pas relisible** : « j'ai retenu SQLite » ne
    se conteste pas ; « j'ai retenu SQLite parce que la tâche ne nomme aucun
    serveur et que le livrable doit tourner sans service » se conteste, et c'est
    tout ce qui rend l'autonomie vérifiable après coup (#1019).

    ⚠ C'est l'entrée **exécuteur** qui est éprouvée ici : le double appelle
    `on_decision` en direct, donc `_consigne_decision_autonome` — sans passer par
    l'outil MCP, qui aurait écarté la raison vide avant elle. Ce chemin a deux
    entrées et sa garde est écrite deux fois exprès ; les éprouver ensemble
    laisserait la seconde libre de dériver. L'autre entrée est
    `test_l_outil_ne_consigne_rien_sans_decision_ni_sans_raison`.

    Le témoin compte autant que le cas : prouver qu'une raison vide n'écrit rien
    ne dit pas que le verbe écrit quoi que ce soit.
    """
    # ① La raison vide : rien.
    provider = ConsigneUneDecision(raison="   ")
    _, journal = _run(provider)

    assert provider.consignees == 1  # le verbe a bien été appelé…
    assert _decisions(journal) == []  # … et n'a rien écrit.

    # ② La décision vide : rien non plus — consigner « j'ai décidé » sans dire
    # quoi remplirait le journal de ce qu'on savait déjà, qu'un agent travaille.
    provider = ConsigneUneDecision(decision="  ")
    _, journal = _run(provider)

    assert provider.consignees == 1
    assert _decisions(journal) == []

    # ③ Le témoin : la même expérience complète écrit, elle.
    _, journal = _run(ConsigneUneDecision())

    (etape,) = _decisions(journal)
    assert etape.statut == STATUT_DECISION_AUTONOME


def test_l_outil_ne_consigne_rien_sans_decision_ni_sans_raison():
    """L'autre entrée du même refus : ce que l'agent lit, et ce qu'il ne déclenche pas.

    Le canal n'est **pas appelé** — c'est ce qui compte : refuser après coup
    laisserait la ligne vide qu'on veut éviter. Et aucune des deux réponses n'est
    une **erreur d'outil** : une erreur invite à réessayer *le même appel*, or ici
    il faut le rappeler **autrement**.
    """
    appels: list[tuple[str, str]] = []

    def canal(decision: str, raison: str) -> None:
        appels.append((decision, raison))

    outil = _outil_decision(canal)

    assert _appelle_outil(outil, decision="", raison=RAISON) == (
        decision_mod.DECISION_MANQUANTE
    )
    assert _appelle_outil(outil, decision=DECISION, raison="  ") == (
        decision_mod.RAISON_MANQUANTE
    )
    assert appels == []

    # Le témoin, et l'accusé : c'est écrit, et **personne ne répondra**.
    servi = _appelle_outil(outil, decision=DECISION, raison=RAISON)
    assert servi == decision_mod.DECISION_CONSIGNEE
    assert "Personne ne va te répondre" in servi
    assert appels == [(DECISION, RAISON)]


def test_un_canal_en_erreur_est_dit_a_l_agent_et_ne_tue_rien():
    """Le troisième cas de l'outil : le callback a levé.

    On le **dit** — sinon l'agent croirait sa décision écrite et ne la répéterait
    pas dans son compte-rendu final, seul endroit qui lui reste — et on ne laisse
    surtout pas l'exception remonter : elle tuerait la tâche à l'instant précis
    où l'agent rend compte de lui-même, ce qui est la pire des façons de lui
    apprendre à le faire.
    """
    def canal(decision: str, raison: str) -> None:
        raise RuntimeError("journal injoignable")

    servi = _appelle_outil(_outil_decision(canal), decision=DECISION, raison=RAISON)

    assert "journal injoignable" in servi
    assert "compte-rendu" in servi


def test_le_verbe_porte_le_nom_sous_lequel_une_politique_le_designe():
    """Le nom complet est un **contrat** (docs/04 §1.4ter), et il n'avait aucun lecteur.

    Le droit de ce verbe est tenu par la couche permissions (#110) : une
    politique cite `mcp__maestro__<nom>`, l'autorise ou le refuse. Un renommage
    de `NOM_OUTIL` emporterait `OUTIL_DECISION` en silence, et une politique
    écrite sur l'ancien nom cesserait de désigner quoi que ce soit — sans que
    rien ne rougisse.
    """
    assert decision_mod.OUTIL_DECISION == f"mcp__{NOM_SERVEUR}__{decision_mod.NOM_OUTIL}"
    assert decision_mod.OUTIL_DECISION == "mcp__maestro__consigner_decision"


def test_le_verbe_n_est_monte_que_si_un_canal_lui_est_cable():
    """Un verbe qui promettrait une trace sans pouvoir l'écrire serait pire qu'absent.

    C'est la règle du porte-outils (#718) : les canaux sont indépendants, la
    liste rendue peut être vide, et **la seule chose que ce verbe promet est la
    trace**. Sans journal, il n'est pas monté du tout.
    """
    def canal(decision: str, raison: str) -> None:  # pragma: no cover — jamais appelé
        return None

    assert _outils_maestro() == []
    assert [outil.name for outil in _outils_maestro(on_decision=canal)] == [
        decision_mod.NOM_OUTIL
    ]


# --- ② Ce que la trace porte -------------------------------------------------------------


def test_la_trace_separe_la_decision_de_son_motif():
    """L'engagement de #1024, et la seule raison pour laquelle #1026 n'a rien à découper.

    `sortie` porte **ce qui a été tranché**, `description` **pourquoi**. Les
    fondre en un seul texte obligerait la vue des décisions à les redécouper,
    c'est-à-dire à deviner par la forme ce que le journal savait à l'écriture.

    Les deux autres moitiés de la ligne sont **fermées par l'exécuteur** (même
    règle qu'en #582 et #720) : l'agent et la tâche. Un agent qui les fournirait
    pourrait signer d'un autre nom, ou rattacher sa décision à la tâche d'un
    tiers.

    Usage nul, comme le blocage (#719) et pour la même raison : rendre compte de
    soi ne doit rien coûter, faute de quoi se taire serait la stratégie payante.
    """
    _, journal = _run(ConsigneUneDecision())

    (etape,) = _decisions(journal)
    assert etape.etape == f"api{SUFFIXE_ETAPE_DECISION}"
    assert etape.statut == STATUT_DECISION_AUTONOME
    assert etape.sortie == DECISION
    assert etape.description == RAISON
    assert etape.agent and etape.role
    assert etape.run_id == RUN
    assert etape.usage.cout_usd in (None, 0.0)


def test_une_decision_consignee_ne_change_pas_le_statut_de_la_tache():
    """Un agent qui décide **dans** sa tâche ne décide pas **du sort** de sa tâche.

    C'est le refus de docs/31 §3.4, et la moitié invisible du verbe : rien
    n'échouerait s'il cédait — la tâche changerait seulement de colonne, et son
    aval avec elle par la cascade de #43.
    """
    provider = ConsigneUneDecision()

    rapport, _ = _run(provider)

    assert [r.task_id for r in rapport.resultats] == ["api", "recette"]
    assert [r.ok for r in rapport.resultats] == [True, True]
    assert all(r.statut != STATUT_DECISION_AUTONOME for r in rapport.resultats)


# --- ③ La lecture : deux familles, jamais mêlées ------------------------------------------


def test_les_deux_familles_sont_rendues_et_ne_se_confondent_pas():
    """« Je n'avais pas à demander » et « personne n'a répondu » ne sont pas le même fait.

    Les ranger sous un même mot reviendrait à ne plus pouvoir dire lequel des
    deux on lit — c'est le partage que `tache.decision` fait déjà entre la
    décision d'un **agent** et celle d'une **personne**. `origine` les sépare, et
    `hypothese` est servi comme booléen pour que la marque à l'écran ne dépende
    pas d'une comparaison de chaîne côté client.

    Les deux sortent dans les **mêmes colonnes**, sans rien redécouper : c'est
    tout ce que la séparation `sortie`/`description` du bloc ② achetait.
    """
    lecture = decisions_du_run(
        RUN, [_tranchee(3), _hypothese(5, tache_id="schema")], taches={"api": "Exposer l'API"}
    )

    assert lecture.total == 2
    assert lecture.hypotheses == 1
    tranchee, hypothese = sorted(lecture.entrees, key=lambda d: d.rang)

    assert tranchee.origine == ORIGINE_TRANCHEE
    assert tranchee.hypothese is False
    assert (tranchee.decision, tranchee.raison) == (DECISION, RAISON)
    # L'identifiant de l'entrée de journal dont elle vient : rien n'est créé, et
    # c'est ce qui permet de la retrouver dans `GET /api/journal`.
    assert tranchee.id == "j-0003"
    # Le **titre** de la tâche, résolu depuis la projection — pas le `nom` de
    # l'étape, qui le porte préfixé.
    assert tranchee.tache == "Exposer l'API"

    assert hypothese.origine == ORIGINE_HYPOTHESE
    assert hypothese.hypothese is True
    assert hypothese.decision == "je pars sur SQLite"
    assert "240" in hypothese.raison
    # Tâche absente de la table : l'identifiant reste un renvoi valable, et rien
    # n'est perdu faute de connaître un titre.
    assert hypothese.tache == ""
    assert hypothese.tache_id == "schema"


def test_une_question_a_laquelle_on_a_repondu_n_est_pas_une_decision_autonome():
    """Quelqu'un a répondu : c'est le **contraire** de l'autonomie.

    Le tri se fait sur le type d'abord, sur le `statut` ensuite et seulement pour
    la seconde famille. Une étape de question soldée par une réponse reste où
    elle est, au journal du run — la faire entrer ici gonflerait la liste de
    décisions qui ne sont pas d'un agent, c'est-à-dire la rendrait fausse dans le
    seul cas où elle compte.
    """
    lecture = decisions_du_run(
        RUN,
        [
            _entree(
                1,
                EVENEMENT_AGENT_ACTIVITE,
                statut=STATUT_QUESTION_REPONDUE,
                detail="Postgres",
                description="réponse reçue à : Postgres ou SQLite ?",
            ),
            # Et tout le bruit de fond ordinaire d'un run, qui n'y entre pas non plus.
            _entree(2, EVENEMENT_TACHE_STATUT, statut="terminee"),
            _entree(3, EVENEMENT_AGENT_ACTIVITE, statut="relance"),
        ],
    )

    assert lecture.entrees == ()
    assert lecture.total == 0
    assert lecture.hypotheses == 0


def test_la_liste_va_du_plus_recent_au_plus_ancien_et_departage_par_le_rang():
    """Le sens de lecture est celui du **journal du run**, pas celui de la frise.

    Les deux lectures chronologiques de la bascule doivent aller dans le même
    sens, faute de quoi passer de l'une à l'autre demanderait de relire le sens
    avant la première ligne.

    Le départage par le rang n'est pas un détail : les horodatages du dépôt sont
    **à la seconde**, et une salve d'agents parallèles en produit couramment deux
    identiques. Sans lui, deux décisions de la même seconde sauteraient d'un
    rafraîchissement à l'autre.
    """
    meme_seconde = "2026-09-20T17:04:11+00:00"
    lecture = decisions_du_run(
        RUN,
        [
            _tranchee(1, horodatage="2026-09-20T17:00:00+00:00"),
            _tranchee(2, horodatage=meme_seconde),
            _tranchee(3, horodatage=meme_seconde),
            _tranchee(4, horodatage="2026-09-20T17:09:00+00:00"),
        ],
    )

    # Le plus récent d'abord ; à instant égal, l'ordre du journal, tenu.
    assert [d.rang for d in lecture.entrees] == [4, 3, 2, 1]


def test_le_plafond_retient_les_plus_recentes_et_le_dit():
    """Une borne **muette** ferait passer un run bavard pour un run sobre.

    `total` et `hypotheses` comptent **avant** le plafond : les recompter sur ce
    qui a été rendu donnerait faux dès que la liste est tronquée — et c'est le
    compte affiché à côté du titre de la vue.
    """
    entrees = [_tranchee(rang) for rang in range(1, 6)]
    entrees.append(_hypothese(6))

    lecture = decisions_du_run(RUN, entrees, plafond=2)

    assert [d.rang for d in lecture.entrees] == [6, 5]
    assert lecture.total == 6
    assert lecture.hypotheses == 1
    assert lecture.tronquee is True
    assert lecture.plafond == 2

    # Sans troncature, la liste ne se dit pas tronquée — et le plafond servi est
    # celui du module, que l'écran affiche tel quel.
    entiere = decisions_du_run(RUN, entrees)
    assert entiere.tronquee is False
    assert entiere.plafond == PLAFOND_DECISIONS


def test_un_run_sans_aucune_decision_rend_une_liste_vide_et_pas_une_erreur():
    """« Rien n'a été décidé seul » est une **réponse**, pas une absence de vue.

    C'est ce que l'écran dit quand la liste est vide (aucun agent n'a eu à
    trancher hors de son brief, aucune question n'est restée sans réponse), et il
    faut donc que la composition sache rendre ce cas sans rien inventer.
    """
    lecture = decisions_du_run(RUN, [])

    assert lecture.to_dict() == {
        "run_id": RUN,
        "entrees": [],
        "total": 0,
        "hypotheses": 0,
        "plafond": PLAFOND_DECISIONS,
        "tronquee": False,
    }


# --- ④ La route ---------------------------------------------------------------------------


def _client_sur(*evenements: Event) -> TestClient:
    """L'app réelle, bus mémoire, historique rejoué par le lifespan.

    Le rejeu alimente **la projection et le journal requêtable** — c'est ce qui
    permet à la liste de se composer sans qu'aucun test n'ait à remplir l'un ou
    l'autre à la main. Même harnais que `tests/test_frise_run.py`, sur la lecture
    voisine.
    """
    log = InMemoryEventLog()
    for event in evenements:
        asyncio.run(log.consigner(event))
    return TestClient(
        create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
    )


def _evenement_decision(**champs) -> Event:
    """L'événement `tache.decision` tel que le pont le fait remonter (#1024)."""
    defauts = {
        "run_id": RUN,
        "tache_id": "api",
        "titre": "Décision de l'agent — Exposer l'API",
        "agent": "developpeur",
        "role": "Développeur",
        "statut": STATUT_DECISION_AUTONOME,
        "detail": DECISION,
        "description": RAISON,
        "projet_id": PROJET,
    }
    return Event(type=EVENEMENT_TACHE_DECISION, **{**defauts, **champs})


def test_la_route_rend_les_decisions_du_run_avec_le_titre_de_leur_tache():
    """Le titre vient de la **projection**, jamais du `nom` de l'étape de journal.

    Celui-ci le porte préfixé (« Décision de l'agent — … ») : le découper
    reviendrait à deviner par la forme ce que la projection sait déjà, et un
    changement de libellé ferait alors apparaître le préfixe à l'écran.

    La ligne garde l'identifiant que `GET /api/journal?run_id=…` lui donne : les
    deux lectures ne peuvent pas se contredire, puisque c'est la même entrée.
    """
    client = _client_sur(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_EN_COURS,
            titre="Objectif",
            projet_id=PROJET,
        ),
        Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id=RUN,
            tache_id="api",
            titre="Exposer l'API",
            agent="developpeur",
            role="Développeur",
            statut="en_cours",
            projet_id=PROJET,
        ),
        _evenement_decision(),
    )

    with client:
        lecture = client.get(f"/api/executions/{RUN}/decisions").json()
        entrees_journal = client.get(
            f"/api/journal?projet=tous&run_id={RUN}"
        ).json()["entrees"]

    assert lecture["run_id"] == RUN
    assert lecture["total"] == 1
    assert lecture["hypotheses"] == 0
    (ligne,) = lecture["entrees"]
    assert ligne["origine"] == ORIGINE_TRANCHEE
    assert ligne["hypothese"] is False
    assert ligne["decision"] == DECISION
    assert ligne["raison"] == RAISON
    assert ligne["tache"] == "Exposer l'API"
    assert ligne["agent"] == "developpeur"
    assert ligne["role"] == "Développeur"

    # Rien n'est créé : la ligne **est** une entrée du journal, et porte son id.
    assert ligne["id"] in {entree["id"] for entree in entrees_journal}


def test_la_route_ignore_un_run_voisin_et_refuse_un_run_inconnu():
    """`404` sur un run dont la projection n'a aucune trace, comme `/cout` et `/frise`.

    Une liste vide se lirait « ce run n'a rien décidé seul », ce qui est une
    réponse — et pas la bonne quand le run n'existe pas. Le cloisonnement, lui,
    est la promesse de base : une décision appartient au run où elle a été prise.
    """
    client = _client_sur(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_EN_COURS,
            titre="Objectif",
            projet_id=PROJET,
        ),
        _evenement_decision(),
        _evenement_decision(run_id="run-voisin", detail="Autre chose"),
    )

    with client:
        lecture = client.get(f"/api/executions/{RUN}/decisions").json()
        inconnu = client.get("/api/executions/jamais-vu/decisions")

    assert [ligne["decision"] for ligne in lecture["entrees"]] == [DECISION]
    assert inconnu.status_code == 404
