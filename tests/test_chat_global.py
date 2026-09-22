"""Tests du **fil global** — le canal `orchestrateur` (tickets #273, #685, #686).

Le chat de la Control Tower avait deux canaux couverts et il lui en manquait un.
`tests/test_chat.py` (#84) éprouve le `ServiceChat` — persistance, acheminement,
diffusion — et `tests/test_controltower.py` ses endpoints. Le **fil global**
(#268, `maestro/controltower/orchestration.py`) n'avait, lui, aucun test : ni sa
règle d'intention, ni son aperçu, ni son répondeur, ni la route qui le sert. Or
c'est le seul canal du produit qui **agit** — une phrase mal reconnue y ouvre un
run, c'est-à-dire du quota et des écritures dans un projet.

Depuis #685 le juge a changé : le lexique (`_AMORCES`/`_VERBES_TRAVAIL`) est
parti en entier, c'est le **modèle** qui rend le verdict avec sa réponse, et
**aucun run ne s'ouvre sans accord explicite**. Ce que ces tests peuvent tenir
sans modèle est donc exactement ce qui appartient au canal, et rien de ce qui
appartient au jugement : le fournisseur est un double scripté, ce que le module
permet en ne lui demandant qu'un `generate`.

Aucun réseau, aucun modèle, aucun moteur : le lanceur de run est lui aussi un
double qui enregistre ce qu'on lui demande, et c'est exactement ce que le module
permet en n'exigeant qu'un `LanceurRun` (« ouvre un run sur cet objectif »).

Couvre :

① **le canal ne filtre plus rien** — les cinq formulations du tableau de #682
   atteignent le modèle et aboutissent à une proposition, sans qu'aucun run ne
   s'ouvre ; un verdict illisible vaut un échange ;
② **l'aperçu** de l'orchestration : la phrase d'état, ses accords, et le fait
   qu'elle soit relue à chaque question plutôt que figée à la construction ;
③ **le répondeur** : le run ouvert **sur l'objectif approuvé** et rattaché à la
   réponse, un lancement en échec raconté dans le fil au lieu d'être levé, le
   canal sans lanceur qui le dit plutôt que de faire semblant, et — depuis #686 —
   le **juge injoignable** qui se dit lui aussi : la cause nommée, sa famille
   (panne passagère / réglage absent) lue à l'endroit de l'échec, rien d'ouvert
   ni de proposé, et la même phrase sur un « oui » que sur une demande ;
④ **le contrat SSE** vu du répondeur : la concaténation des incréments *est* le
   texte final — ce dont dépend un client qui reconstitue la réponse des `delta`
   seuls ;
⑤ **les endpoints** : `/api/chat/orchestrateur` sert un fil que le catalogue ne
   porte pas, le run ouvert voyage jusqu'au JSON et jusqu'au WebSocket, et le
   flux rend `debut`/`fragment`/`fin` ;
⑥-⑦ **le projet de la fenêtre** (#683), du corps de la requête jusqu'au run qui
   figure dans la liste de l'écran ;
⑧ **le protocole d'accord joué en entier** (#688) : deux tours sur le même
   répondeur, où la proposition n'ouvre rien et où seul l'accord ouvre — le
   refus, le changement de sujet et le **silence** ne faisant rien ;
⑨ **le juge jouable sans fournisseur** (#688) : la résolution est paresseuse et
   `orchestration_repondeur` est le point par lequel toute cette suite juge sans
   modèle, ce dont dépend la règle de `tests/conftest.py` (#195) ;
⑩ **le lexique ne revient pas** (#688) : les symboles retirés en #685 ne sont ni
   définis ni référencés, y compris comme repli — cherchés dans l'arbre
   syntaxique, jamais par un `grep` qui condamnerait les prose qui les racontent.

Ce que ces tests **ne** peuvent pas tenir, et l'assument : la qualité du jugement
lui-même. Le juge est un double, donc « cette phrase est-elle une demande de
travail ? » n'est pas une question qu'on pose ici — on tient que la phrase
**atteint** le juge, que son verdict décide seul, et qu'aucun run ne part sans
accord. Le reste est du ressort du prompt, et se mesure en usage.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.store import AgentStore
from maestro.controltower.app import create_app
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.controltower.causes import CAUSE_PLAFOND_COUT
from maestro.controltower.chat import (
    CONVERSATION_ORIGINE,
    FRAGMENT_CHAT_DEBUT,
    FRAGMENT_CHAT_DELTA,
    FRAGMENT_CHAT_FIN,
    CadrageIntrouvable,
    ChatStore,
    DemandeRecrutement,
    MessageChat,
    RecrutementIntrouvable,
    RepondeurScripte,
    proposition_en_attente,
    recrutement_en_attente,
)
from maestro.controltower.events import (
    EVENEMENT_CHAT_MESSAGE,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
    InMemoryEventBus,
)
from maestro.controltower.orchestration import (
    # Le texte du prompt lui-même est l'objet du critère 2 : il se lit ici, comme
    # `test_registre_de_langue` le lit déjà pour la consigne de registre.
    _PROMPT_ORCHESTRATION,
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    VERDICT_ACCORD,
    VERDICT_ECHANGE,
    VERDICT_PROPOSITION,
    RepondeurOrchestration,
    apercu_de,
    contexte_du_fil,
    faits_des_runs,
    runs_du_fil,
)
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.state import (
    EXECUTION_ECHEC,
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    VALIDATION_EN_ATTENTE,
    ControlTowerState,
)
from maestro.engine import RunReport
from maestro.equipe import RoleValide, definition
from maestro.projets import ProjetStore
from maestro.providers.base import ModelProvider

UTILISATEUR = "utilisateur"

#: L'objectif que le modèle reformule dans sa proposition, puis recopie quand
#: l'utilisateur l'approuve. Il ne ressemble à **aucun** message du fil : c'est ce
#: qui rend visible, à l'assertion, que ce n'est pas le message brut qui part.
OBJECTIF = "Développer une application Windows d'agenda aux fonctionnalités de base"


def _verdict(nom: str, reponse: str, objectif: str = "") -> str:
    """La réponse du modèle telle que le contrat de `_PROMPT_ORCHESTRATION` la décrit."""
    return json.dumps(
        {"verdict": nom, "objectif": objectif, "reponse": reponse}, ensure_ascii=False
    )


def _propose(objectif: str = OBJECTIF) -> str:
    """La phrase d'une proposition, telle que le fil la garde — la mémoire du canal."""
    return f"J'ouvrirais un run sur : « {objectif} ». On y va ?"


class JugeScripte(ModelProvider):
    """Fournisseur factice : rend le verdict qu'on lui a posé, note ce qu'on lui donne.

    Il enregistre les prompts pour que les tests puissent vérifier ce qui *atteint*
    le juge — c'est la moitié de #685 qu'aucune assertion sur le verdict ne
    couvrirait : le canal ne doit plus écarter une formulation avant l'appel.
    """

    name = "juge-scripte"

    def __init__(self, reponse: str) -> None:
        self.reponse = reponse
        self.prompts: list[str] = []
        self.systemes: list[str | None] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        self.prompts.append(prompt)
        self.systemes.append(system_prompt)
        return self.reponse


def _fil(*contenus: str) -> list[MessageChat]:
    """Un fil dont les messages alternent utilisateur / orchestrateur, l'utilisateur d'abord."""
    return [
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=UTILISATEUR if rang % 2 == 0 else NOM_ORCHESTRATION,
            contenu=contenu,
        )
        for rang, contenu in enumerate(contenus)
    ]


class LanceurEspion:
    """Un `LanceurRun` qui note ce qu'on lui demande — aucun moteur, aucun quota.

    Il note **les quatre** arguments du contrat : l'objectif, le projet de la
    fenêtre (#683), les bornes du run (#990) et le contexte des sources de la
    conversation (#1172). Un double qui n'accepterait que
    les premiers rendrait le canal vert sur un lancement que le vrai service
    refuserait — et le répondeur rattrapant toute exception du lanceur, l'échec
    se lirait « le lancement a échoué » au lieu d'une erreur de signature.
    """

    def __init__(self, *, run_id: str = "run-42", statut: str = "en_cours") -> None:
        self.objectifs: list[str] = []
        self.projets: list[str | None] = []
        self.bornes: list[BornesRun] = []
        self.contextes: list[str] = []
        self._resume = {"run_id": run_id, "statut": statut}

    async def __call__(
        self,
        objectif: str,
        projet_id: str | None = None,
        bornes: BornesRun = AUCUNE_BORNE,
        contexte_sources: str = "",
    ) -> dict[str, str]:
        self.objectifs.append(objectif)
        self.projets.append(projet_id)
        self.bornes.append(bornes)
        self.contextes.append(contexte_sources)
        return dict(self._resume)


def _repondeur(
    reponse_du_juge: str,
    *,
    lanceur: LanceurEspion | None = None,
    apercu: Any = None,
    faits: Any = None,
) -> tuple[RepondeurOrchestration, JugeScripte]:
    """Le répondeur et son juge, montés ensemble — les tests ont besoin des deux."""
    juge = JugeScripte(reponse_du_juge)
    return (
        RepondeurOrchestration(
            lanceur=lanceur, apercu=apercu, faits=faits, provider=juge
        ),
        juge,
    )


# ── ① le canal ne filtre plus rien, et n'ouvre rien de lui-même ───────────────
#
# Le lexique d'avant #685 tranchait **avant** tout appel modèle : les cinq
# formulations ci-dessous rendaient `echange` et n'atteignaient jamais le juge.
# Ce que ces tests tiennent est donc exactement ce qui appartient au canal — le
# message arrive au modèle, et le verdict du modèle décide seul de la suite.


#: Le banc de #682, **cause par cause** : la formulation, et la raison précise
#: pour laquelle le lexique la faisait taire. La cause est portée par l'`id` du
#: cas plutôt que par un commentaire, si bien que la sortie de pytest nomme celle
#: qui vient de lâcher — un banc dont les cinq lignes s'appelleraient `demande0`
#: à `demande4` dirait qu'il a rougi, jamais **laquelle** garde (#688).
#:
#: Les quatre premières causes tenaient **ensemble** dans la phrase réellement
#: envoyée (« J'aimerai que tu me génère le projet p1 … ») : les éprouver séparées
#: est ce qui empêche qu'un correctif n'en traite qu'une et que le banc passe
#: quand même.
BANC_682 = [
    pytest.param("Génère une application d'agenda", id="verbe-hors-liste"),
    pytest.param("J'aimerai ajouter la pagination", id="amorce-sans-s"),
    pytest.param("J'aimerais que tu ajoutes la pagination", id="subordonnee-que-tu"),
    pytest.param("Peux-tu me créer une application", id="pronom-objet-intercale"),
    pytest.param("Il faudrait que tu corriges le tri", id="subordonnee-et-conjugaison"),
]

#: Les témoins **positifs** du tableau : ceux que le lexique reconnaissait déjà.
#: Ils sont à part parce qu'ils ne prouvent pas la même chose — les cinq d'au-
#: dessus disent qu'un silence a cessé, ceux-ci qu'aucune reconnaissance acquise
#: n'a été perdue en chemin. Fondus dans un seul banc, un correctif qui les aurait
#: cassés tous les deux se lirait comme un banc à moitié rouge.
TEMOINS_QUI_PASSAIENT_DEJA = [
    pytest.param("Ajoute la pagination", id="imperatif-nu"),
    pytest.param("Crée-moi une application d'agenda", id="imperatif-avec-pronom"),
]

#: Les témoins **négatifs** : ce qui n'est pas une demande de travail et ne doit
#: produire aucune proposition. Ils sont la moitié qui empêche de rendre le banc
#: vert en proposant un run sur tout — un juge qui propose toujours passerait les
#: sept cas ci-dessus et échouerait ici.
TEMOINS_NEGATIFS = [
    pytest.param("Comment ajouter une page ?", id="question-sur-l-outil"),
    pytest.param("Où en sont les runs ?", id="demande-d-etat"),
    pytest.param("merci", id="salutation"),
]


@pytest.mark.parametrize("demande", BANC_682 + TEMOINS_QUI_PASSAIENT_DEJA)
def test_une_demande_de_travail_est_proposee_et_n_ouvre_aucun_run(demande: str) -> None:
    """Critère 1 : une proposition, jamais un run — et la phrase atteint le juge."""
    lanceur = LanceurEspion()
    repondeur, juge = _repondeur(
        _verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF), lanceur=lanceur
    )

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil(demande)))

    # L'échantillon fautif de #682 : c'est ici que le lexique s'arrêtait.
    assert demande in juge.prompts[0]
    # Proposer n'ouvre rien — le sujet même du lot.
    assert lanceur.objectifs == []
    assert reponse.run_id == ""
    assert OBJECTIF in reponse.contenu


@pytest.mark.parametrize("demande", BANC_682)
def test_le_canal_ne_tranche_plus_avant_le_juge(demande: str) -> None:
    """La moitié de #685 qu'aucune assertion sur le verdict ne couvre (#688).

    Le lexique rendait son verdict **sans appeler personne** : les cinq
    formulations n'atteignaient jamais le modèle. Ce que ce test tient est donc
    l'inverse exact — le canal appelle le juge **une fois**, sur le fil entier, et
    quel que soit le verdict qui reviendra. Il ne peut pas se confondre avec le
    test au-dessus : celui-là scripte une proposition et vérifie ce qui en sort,
    celui-ci vérifie qu'il y a **eu** un appel, et le scripte en `echange` — le
    verdict le moins favorable, celui que le lexique rendait.
    """
    lanceur = LanceurEspion()
    repondeur, juge = _repondeur(
        _verdict(VERDICT_ECHANGE, "Je ne suis pas sûr de comprendre."), lanceur=lanceur
    )

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil(demande)))

    assert len(juge.prompts) == 1
    assert demande in juge.prompts[0]
    # Et même écarté, rien ne s'ouvre : le canal n'a pas de seconde voie.
    assert lanceur.objectifs == []


@pytest.mark.parametrize("demande", TEMOINS_NEGATIFS)
def test_une_question_un_etat_ou_une_salutation_n_ouvrent_rien(demande: str) -> None:
    """La seconde moitié du critère 1 : ce qui n'est pas un travail reste une conversation."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ECHANGE, "Aucun run en cours."), lanceur=lanceur
    )

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil(demande)))

    assert lanceur.objectifs == []
    assert reponse.run_id == ""


def test_un_refus_n_ouvre_rien() -> None:
    """Critère 2 : un « non » derrière une proposition laisse le fil intact."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ECHANGE, "Entendu, je n'ouvre rien."), lanceur=lanceur
    )

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION,
            _fil("Génère une application d'agenda", _propose(), "non"),
        )
    )

    assert lanceur.objectifs == []
    assert reponse.run_id == ""


def test_un_verdict_illisible_vaut_un_echange() -> None:
    """Une réponse hors contrat coûte une reformulation, jamais un run ni un 502.

    C'est l'asymétrie du module portée à l'analyse : ce qu'on ne comprend pas ne
    peut pas valoir un accord. Le texte du modèle est rendu tel quel — le canal
    est depuis #666 la seule porte d'entrée, laisser l'utilisateur devant une
    erreur serait pire que devant une phrase.
    """
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur("Bien sûr, je m'en occupe !", lanceur=lanceur)

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert lanceur.objectifs == []
    assert reponse.run_id == ""
    assert reponse.contenu == "Bien sûr, je m'en occupe !"


def test_un_verdict_inconnu_ne_vaut_pas_un_accord() -> None:
    """La liste des verdicts est **blanche** : un mot inattendu retombe sur l'échange."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict("lancer", "C'est parti.", OBJECTIF), lanceur=lanceur
    )

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("oui")))

    assert lanceur.objectifs == []


def test_le_verdict_se_lit_aussi_dans_un_bloc_de_code() -> None:
    """Les modèles encadrent volontiers un JSON qu'on leur a demandé nu."""
    lanceur = LanceurEspion()
    corps = _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF)
    repondeur, _ = _repondeur(f"Voici ma décision :\n```json\n{corps}\n```\n", lanceur=lanceur)

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("oui")))

    assert lanceur.objectifs == [OBJECTIF]


def test_le_prompt_porte_le_fil_entier_et_l_etat() -> None:
    """La mémoire du canal est le fil (#685) : sa propre proposition, et la réponse.

    Sans elle, juger « oui » demanderait un second lexique — juste après en avoir
    retiré un. L'état de l'orchestration entre dans le même prompt : c'est ce qui
    permet de répondre « où en est-on ? » sans voie séparée.
    """
    repondeur, juge = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF),
        lanceur=LanceurEspion(),
        apercu=lambda projet_id=None: "1 run en cours, 3 tâches suivies.",
    )

    asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION,
            _fil("Génère une application d'agenda", _propose(), "oui"),
        )
    )

    prompt = juge.prompts[0]
    assert "Génère une application d'agenda" in prompt
    assert OBJECTIF in prompt
    assert "1 run en cours, 3 tâches suivies." in prompt
    # Le contrat de sortie voyage en prompt système, jamais mêlé à la conversation.
    assert "verdict" in (juge.systemes[0] or "")


# ── ② l'aperçu de l'orchestration ─────────────────────────────────────────────


def _run(run_id: str, statut: str, projet_id: str | None = None) -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT, run_id=run_id, statut=statut, projet_id=projet_id
    )


def _tache(tache_id: str, run_id: str, projet_id: str | None = None) -> Event:
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id=tache_id,
        titre="Écrire les tests",
        agent="qa",
        role="QA / Testeur",
        statut="en_cours",
        projet_id=projet_id,
    )


def _validation(tache_id: str) -> Event:
    return Event(
        type=EVENEMENT_VALIDATION_DEMANDE,
        tache_id=tache_id,
        titre="Déployer en production",
        agent="devops",
        role="DevOps / SRE",
        statut=VALIDATION_EN_ATTENTE,
    )


def test_apercu_sans_rien_le_dit_en_une_phrase() -> None:
    assert apercu_de(ControlTowerState())() == "Aucun run en cours."


def test_apercu_compte_les_runs_actifs_et_les_taches() -> None:
    state = ControlTowerState()
    state.appliquer(_run("run-1", EXECUTION_EN_COURS))
    state.appliquer(_tache("T-1", "run-1"))

    assert apercu_de(state)() == "1 run en cours, 1 tâche suivie."


def test_un_run_qui_attend_un_arbitrage_compte_pour_un_run_en_cours() -> None:
    """De la place où l'on pose la question, un run qui attend est un run en cours."""
    state = ControlTowerState()
    state.appliquer(_run("run-1", EXECUTION_EN_ATTENTE_ARBITRAGE))

    assert apercu_de(state)().startswith("1 run en cours")


def test_un_run_solde_ne_compte_plus() -> None:
    state = ControlTowerState()
    state.appliquer(_run("run-1", EXECUTION_TERMINEE))

    assert apercu_de(state)() == "Aucun run en cours."


def test_l_apercu_nomme_les_validations_en_attente() -> None:
    state = ControlTowerState()
    state.appliquer(_run("run-1", EXECUTION_EN_COURS))
    state.appliquer(_validation("T-1"))
    state.appliquer(_validation("T-2"))

    assert "2 validations attendent votre arbitrage." in apercu_de(state)()


def test_l_apercu_est_relu_a_chaque_question() -> None:
    """Figé à la construction de l'app, il annoncerait l'état d'hier."""
    state = ControlTowerState()
    apercu = apercu_de(state)
    assert apercu() == "Aucun run en cours."

    state.appliquer(_run("run-1", EXECUTION_EN_COURS))

    assert apercu() != "Aucun run en cours."


def test_l_apercu_ne_compte_que_ce_que_l_ecran_peut_montrer() -> None:
    """La seconde moitié de #683 : la phrase et l'écran parlaient de deux périmètres.

    Le fil annonçait « 1 run en cours » en comptant *tous* les runs du poste,
    quand chaque vue de travail est cadrée sur le projet actif (#277) — d'où un
    run annoncé en cours, absent de la liste et refusé par la vue de détail.
    L'aperçu prend donc le projet de la fenêtre, et ses **trois** compteurs avec
    lui : compter les runs d'un projet et les tâches de tous ferait une phrase
    qui se contredit d'une virgule à l'autre.
    """
    state = ControlTowerState()
    state.appliquer(_run("run-ici", EXECUTION_EN_COURS, projet_id="prj-ici"))
    state.appliquer(_tache("T-ici", "run-ici", projet_id="prj-ici"))
    state.appliquer(_run("run-ailleurs", EXECUTION_EN_COURS, projet_id="prj-ailleurs"))
    state.appliquer(_tache("T-ailleurs", "run-ailleurs", projet_id="prj-ailleurs"))
    apercu = apercu_de(state)

    assert apercu("prj-ici") == "1 run en cours, 1 tâche suivie."
    # L'échantillon fautif : sans portée, la phrase est celle d'avant le lot —
    # elle compte les deux projets, donc annonce un travail que l'écran cadré
    # sur « prj-ici » ne montre pas.
    assert apercu() == "2 runs en cours, 2 tâches suivies."


def test_un_run_sans_projet_ne_compte_dans_l_apercu_d_aucun_projet() -> None:
    """La règle de portée, pas une seconde : `PorteeProjet.retient` ne devine rien.

    C'est exactement le run que #683 a trouvé en vol — orphelin, donc dans la
    vue d'aucun projet. Le compter dans celle du projet actif redirait le
    mensonge que ce ticket supprime ; il reste visible sans portée.
    """
    state = ControlTowerState()
    state.appliquer(_run("run-orphelin", EXECUTION_EN_COURS))
    apercu = apercu_de(state)

    assert apercu("prj-ici") == "Aucun run en cours."
    assert apercu().startswith("1 run en cours")


# ── ②bis les faits des runs : ce qu'ils ont fait, et pourquoi l'un a échoué ───
#
# L'aperçu au-dessus **compte** ; ces tests-ci tiennent la seconde lecture, celle
# qui **raconte** (#1157). Le scénario de référence est l'essai réel du
# 2026-09-21 : un run échoué sur un projet sans agent, dont l'issue n'annonçait
# qu'un décompte pendant que chacune de ses tâches portait la cause en clair.

#: Le run de l'essai du 2026-09-21, et le projet sur lequel il a tourné.
RUN_ECHOUE = "8a15f78f45d3"
PROJET = "p1"

#: La cause réelle, telle que le routeur l'écrit sur une tâche que personne ne
#: peut prendre (`maestro.router.router`, repli de #1042). Elle est recopiée ici
#: **mot pour mot** : c'est la phrase que le fil n'avait pas sous les yeux, et le
#: ticket demande qu'elle y soit.
RAISON_SANS_EQUIPE = "aucun agent dans ce catalogue — l'équipe reste à recruter"


def _lancement(run_id: str, objectif: str, projet_id: str | None = None) -> Event:
    """L'événement qui ouvre un run : son objectif, son projet."""
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=run_id,
        statut=EXECUTION_EN_COURS,
        description=objectif,
        projet_id=projet_id,
    )


def _issue(run_id: str, statut: str, detail: str = "", cause: str = "") -> Event:
    """L'événement qui solde un run : son statut terminal, son détail, sa cause."""
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=run_id,
        statut=statut,
        detail=detail,
        cause=cause,
    )


def _etape(
    tache_id: str,
    run_id: str,
    *,
    statut: str,
    detail: str = "",
    titre: str = "Écrire les tests",
    agent: str = "qa",
    role: str = "QA / Testeur",
    projet_id: str | None = None,
) -> Event:
    """Une tâche du run, avec le détail que son issue a écrit (l'erreur, souvent)."""
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id=tache_id,
        titre=titre,
        agent=agent,
        role=role,
        statut=statut,
        detail=detail,
        projet_id=projet_id,
    )


def _state_du_2026_09_21() -> ControlTowerState:
    """La projection de l'essai réel : trois tâches que personne n'a pu prendre.

    Rejouée **par ses événements** et non construite à la main : c'est le chemin
    que la vraie Control Tower emprunte (`bridge` recopie `erreur` dans le
    `detail` d'un `tache.statut`), et un double qui poserait les champs
    directement laisserait passer une lecture qui ne sait pas où regarder.
    """
    state = ControlTowerState()
    state.appliquer(
        _lancement(RUN_ECHOUE, "Générer une application Windows d'agenda", PROJET)
    )
    for rang in (1, 2, 3):
        state.appliquer(
            _etape(
                f"T-{rang}",
                RUN_ECHOUE,
                statut="echec",
                detail=RAISON_SANS_EQUIPE,
                titre=f"Tâche {rang}",
                agent="—",
                role="à assigner",
                projet_id=PROJET,
            )
        )
    state.appliquer(_issue(RUN_ECHOUE, EXECUTION_ECHEC, "0/3 tâche(s) réussie(s)"))
    return state


def test_un_run_echoue_porte_sa_cause_reelle_et_pas_seulement_son_decompte() -> None:
    """Le défaut du 2026-09-21, dans les deux sens (#1157, critère 1).

    L'issue du run ne dit que « 0/3 tâche(s) réussie(s) » — un décompte, qui
    n'explique rien. Ce que le fil doit avoir est ce que les **tâches** ont écrit,
    et c'est la seule raison pour laquelle la lecture descend jusqu'à elles. Les
    deux sont attendus : rapporter le décompte sans les détails redirait le
    silence de l'essai, rapporter les détails sans le décompte perdrait l'issue
    que l'écran, lui, montre.
    """
    faits = faits_des_runs(_state_du_2026_09_21())(PROJET, (RUN_ECHOUE,))

    assert RUN_ECHOUE in faits
    assert "Échec" in faits
    assert "0/3 tâche(s) réussie(s)" in faits
    # La cause réelle, telle que le routeur l'a écrite — et sur les trois tâches.
    assert faits.count(RAISON_SANS_EQUIPE) == 3
    # Et le porteur qui la rend lisible : personne n'a pris ces tâches.
    assert "à assigner" in faits


def test_un_run_termine_se_raconte_termine_avec_ses_taches() -> None:
    """Le second des trois régimes du critère 3 : un run qui a abouti.

    Il compte parce que « qu'est-ce qu'il a produit ? » est l'autre question du
    ticket : un bloc qui ne saurait décrire que l'échec ferait répondre « je n'ai
    pas cette information » à la moitié des questions.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("run-fini", "Ajouter la pagination", PROJET))
    state.appliquer(
        _etape("T-1", "run-fini", statut="terminee", titre="Paginer la liste",
               projet_id=PROJET)
    )
    state.appliquer(_issue("run-fini", EXECUTION_TERMINEE, "1/1 tâche(s) réussie(s)"))

    faits = faits_des_runs(state)(PROJET, ())

    assert "run-fini" in faits
    assert "Terminée" in faits
    assert "Paginer la liste" in faits
    assert "1/1 tâche(s) réussie(s)" in faits


def test_un_run_en_cours_se_raconte_avec_l_etat_de_chaque_tache() -> None:
    """Le troisième régime : un run en vol, dont les tâches ne sont pas au même point.

    C'est le cas où le détail par tâche porte le plus : le statut du run dit
    seulement « En cours », et ce qui répond à « où en est-on ? » est la
    répartition en dessous.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("run-vol", "Câbler le tableau de bord", PROJET))
    state.appliquer(
        _etape("T-1", "run-vol", statut="terminee", titre="Poser la route",
               projet_id=PROJET)
    )
    state.appliquer(
        _etape("T-2", "run-vol", statut="en_cours", titre="Dessiner les tuiles",
               projet_id=PROJET)
    )

    faits = faits_des_runs(state)(PROJET, ())

    assert "En cours" in faits
    assert "Poser la route" in faits
    assert "Terminée" in faits
    assert "Dessiner les tuiles" in faits


def test_la_cause_classee_est_dite_en_mots_d_interface() -> None:
    """#571 une troisième fois : le fil et l'écran ne nomment pas deux arrêts.

    Le code voyage jusqu'à la projection (`CAUSE_*`, #479) ; ce que le fil en dit
    est la **phrase** de `libelleCause`, au mot près. Rendre le code brut
    écrirait « plafond_cout » dans une conversation.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("run-cher", "Tout réécrire", PROJET))
    state.appliquer(
        _issue("run-cher", EXECUTION_ECHEC, "PlafondDepenseDepasse : 12.51 $ > 10 $",
               cause=CAUSE_PLAFOND_COUT)
    )

    faits = faits_des_runs(state)(PROJET, ())

    assert "Plafond de dépense atteint" in faits
    assert CAUSE_PLAFOND_COUT not in faits
    # Le chiffre reste dans le détail de l'issue, qui vient avec.
    assert "12.51 $ > 10 $" in faits


def test_une_cause_que_le_fil_ne_connait_pas_ne_sort_pas_en_code_brut() -> None:
    """Le jour où le backend prendra de l'avance : rien plutôt qu'un identifiant.

    Même conduite que `libelleCause` côté écran, et pour la même raison — le
    détail de l'issue, lui, reste rapporté juste à côté, donc rien n'est perdu.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("run-neuf", "Un objectif", PROJET))
    state.appliquer(
        _issue("run-neuf", EXECUTION_ECHEC, "quelque chose a cassé", cause="cause_inedite")
    )

    faits = faits_des_runs(state)(PROJET, ())

    assert "cause_inedite" not in faits
    assert "quelque chose a cassé" in faits


def test_un_run_ouvert_par_ce_fil_entre_meme_s_il_n_a_pas_de_projet() -> None:
    """« Au moins ceux que ce fil a ouverts » (#1157, critère 1).

    Un run dicté au fil avant #683 est orphelin, donc dans la vue d'aucun
    projet — et c'est pourtant de lui que la conversation parle. Le rattachement
    par `run_id` passe donc **avant** la portée, sans quoi la question la plus
    fréquente porterait sur le seul run que le bloc ne montrerait pas.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("run-orphelin", "Un travail sans projet"))
    state.appliquer(_issue("run-orphelin", EXECUTION_ECHEC, "0/1 tâche(s) réussie(s)"))
    faits = faits_des_runs(state)

    # L'échantillon fautif : cadré sur le projet, ce run n'entre par aucune portée.
    assert faits(PROJET, ()) == ""
    assert "run-orphelin" in faits(PROJET, ("run-orphelin",))


def test_les_runs_d_un_autre_projet_n_entrent_pas_dans_le_bloc() -> None:
    """La règle de portée (#277), la même que l'aperçu : une seule fenêtre à la fois.

    Deux périmètres dans un même prompt feraient un bloc qui se contredit — le
    compteur parlant d'un projet et le récit d'un autre.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("run-ici", "Ici", PROJET))
    state.appliquer(_lancement("run-ailleurs", "Ailleurs", "prj-ailleurs"))

    faits = faits_des_runs(state)(PROJET, ())

    assert "run-ici" in faits
    assert "run-ailleurs" not in faits


def test_sans_aucun_run_le_bloc_est_vide() -> None:
    """Rien à raconter : le bloc disparaît au lieu d'annoncer un vide.

    L'aperçu dit déjà « Aucun run en cours » ; une seconde phrase pour le même
    fait ferait deux façons de dire qu'il ne s'est rien passé.
    """
    assert faits_des_runs(ControlTowerState())(PROJET, ()) == ""


def test_les_faits_sont_relus_a_chaque_question() -> None:
    """Figés à la construction de l'app, ils raconteraient les runs d'hier."""
    state = ControlTowerState()
    faits = faits_des_runs(state)
    assert faits(PROJET, ()) == ""

    state.appliquer(_lancement("run-1", "Un objectif", PROJET))

    assert "run-1" in faits(PROJET, ())


def test_la_lecture_est_bornee_en_runs_et_le_dit() -> None:
    """La borne existe, et elle s'annonce (#1157, critère 1).

    Un historique long ne doit pas faire exploser le prompt — mais une liste
    coupée **en silence** ferait conclure le juge sur un projet qu'il croirait
    connaître en entier. Les deux moitiés se tiennent : on coupe, et on le dit.
    """
    state = ControlTowerState()
    for rang in range(6):
        state.appliquer(_lancement(f"run-{rang}", f"Objectif {rang}", PROJET))

    faits = faits_des_runs(state)(PROJET, ())

    # Les trois plus récents, dans cet ordre — et pas les trois premiers vus.
    assert "run-5" in faits
    assert "run-3" in faits
    assert "run-2" not in faits
    assert "lecture bornée" in faits


def test_un_run_qui_deborde_en_taches_les_compte_au_lieu_de_les_taire() -> None:
    """Même règle un cran plus bas : ce qui dépasse est compté, jamais tu."""
    state = ControlTowerState()
    state.appliquer(_lancement("run-long", "Un gros objectif", PROJET))
    for rang in range(15):
        state.appliquer(
            _etape(f"T-{rang}", "run-long", statut="terminee", titre=f"Tâche {rang}",
                   projet_id=PROJET)
        )

    faits = faits_des_runs(state)(PROJET, ())

    assert "tâches (15)" in faits
    assert "Tâche 0" in faits
    assert "Tâche 14" not in faits
    assert "3 autres tâches de ce run ne sont pas montrées ici." in faits


def test_un_detail_trop_long_est_tronque_en_le_disant() -> None:
    """Une trace entière ne passe pas en prompt, et sa coupe ne se cache pas.

    La première phrase d'une erreur porte la cause, la suite porte la pile : on
    garde la première et on annonce qu'on a coupé, plutôt que de laisser croire
    que l'erreur s'arrêtait là.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("run-verbeux", "Un objectif", PROJET))
    state.appliquer(
        _etape("T-1", "run-verbeux", statut="echec",
               detail="La cause tient en une phrase. " + "pile " * 200,
               projet_id=PROJET)
    )

    faits = faits_des_runs(state)(PROJET, ())

    assert "La cause tient en une phrase." in faits
    assert "(tronqué)" in faits


def test_un_detail_multiligne_ne_casse_pas_la_structure_du_bloc() -> None:
    """Le bloc se lit ligne par ligne : une pile Python s'y lirait comme des tâches.

    C'est le seul endroit où la mise en forme est un fait et non un goût — une
    trace de vingt lignes ferait vingt tâches apparentes sur un run qui en a une.
    """
    state = ControlTowerState()
    state.appliquer(_lancement("run-pile", "Un objectif", PROJET))
    state.appliquer(
        _etape("T-1", "run-pile", statut="echec",
               detail="Traceback :\n  fichier.py, ligne 3\nValueError : non", projet_id=PROJET)
    )

    faits = faits_des_runs(state)(PROJET, ())

    assert "ValueError : non" in faits
    # Une seule ligne de tâche, et la trace tient dedans.
    assert len([ligne for ligne in faits.splitlines() if ligne.strip().startswith("·")]) == 1


def test_runs_du_fil_rend_ce_que_le_fil_a_ouvert_du_plus_recent_au_plus_ancien() -> None:
    """La liste vient du fil lui-même (#268) — rien n'est stocké à côté.

    C'est « le fil est la seule mémoire » (#685) appliqué à ce qu'il a déclenché :
    un fil relu du disque retrouve ses runs, et un même run rattaché deux fois ne
    compte qu'une fois.
    """
    fil = [
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="fais ceci"),
        MessageChat(agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION,
                    contenu="c'est parti", run_id="run-vieux"),
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="et cela"),
        MessageChat(agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION,
                    contenu="c'est parti", run_id="run-neuf"),
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR,
                    contenu="pourquoi il a échoué ?"),
    ]

    assert runs_du_fil(fil) == ("run-neuf", "run-vieux")
    assert runs_du_fil(_fil("bonjour")) == ()


def test_pourquoi_le_run_a_echoue_atteint_le_juge_avec_la_cause(
) -> None:
    """Le critère 1 de bout en bout, jugé **sur le prompt** et jamais sur un lexique.

    Ce que le canal doit à l'utilisateur est que la question *atteigne* le juge
    avec de quoi y répondre — la qualité de la phrase rendue relève du modèle, et
    l'affirmer ici demanderait de reconnaître des mots dans sa réponse, ce que
    #746 refuse. On tient donc l'entrée : le run de ce fil, son statut, et la
    cause réelle sont dans le prompt quand la question est posée.
    """
    state = _state_du_2026_09_21()
    repondeur, juge = _repondeur(
        _verdict(VERDICT_ECHANGE, "Le run a échoué faute d'équipe."),
        apercu=apercu_de(state),
        faits=faits_des_runs(state),
    )
    fil = [
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="génère l'agenda"),
        MessageChat(agent=NOM_ORCHESTRATION, auteur=NOM_ORCHESTRATION,
                    contenu="C'est parti.", run_id=RUN_ECHOUE),
        MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR,
                    contenu="Pourquoi le run a échoué ?"),
    ]

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, fil, projet_id=PROJET))

    prompt = juge.prompts[0]
    assert RUN_ECHOUE in prompt
    assert "Échec" in prompt
    assert RAISON_SANS_EQUIPE in prompt
    # L'aperçu reste là, et il vient **avant** : compter puis raconter.
    assert prompt.index("État de l'orchestration :") < prompt.index("Runs de ce fil")
    # Et la conversation ferme le prompt, comme avant ce lot.
    assert prompt.index("Runs de ce fil") < prompt.index("Pourquoi le run a échoué ?")


def test_sans_lecture_des_faits_le_prompt_est_celui_d_avant_le_lot() -> None:
    """Le bloc est injecté, donc absent quand personne ne le câble.

    C'est ce qui garde jouable tout le reste de la suite sans projection — et
    c'est la même propriété que l'aperçu : le répondeur ne connaît qu'un contrat,
    jamais la projection.
    """
    repondeur, juge = _repondeur(_verdict(VERDICT_ECHANGE, "Bonjour."))

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("bonjour")))

    assert "Runs de ce fil" not in juge.prompts[0]


def test_le_prompt_ne_renvoie_plus_vers_un_ecran_ce_qu_il_a_sous_les_yeux() -> None:
    """Critère 2 : la consigne de renvoi devient **conditionnelle**, et rien de plus.

    Avant ce lot, `_PROMPT_ORCHESTRATION` ordonnait sans condition : « Quand la
    question est "où ça en est ?", renvoie vers un ENDROIT DE L'INTERFACE ». Le
    juge obéissait, et c'est exactement ce que l'essai du 2026-09-21 a rendu.

    Ce qui la remplace tient en deux moitiés qu'on ne peut pas séparer : le
    prompt annonce ce qu'il reçoit (donc ce avec quoi répondre), et il garde
    l'aveu pour ce qu'il n'a pas — la borne de la lecture, ou ce que la
    projection ignore. Retirer la seconde ferait combler le modèle, ce qui est le
    défaut que #686 et #939 ont payé chacun de leur côté.
    """
    # Le prompt est écrit en colonnes de 80 : les retours à la ligne y coupent les
    # phrases n'importe où, et une assertion qui les épouserait rougirait au
    # premier reformatage sans qu'aucune consigne ait changé.
    prompt = " ".join(_PROMPT_ORCHESTRATION.split())

    assert "renvoie vers un ENDROIT DE L'INTERFACE" not in prompt
    # Ce qu'il reçoit est annoncé : sans cette phrase, les faits arriveraient dans
    # un prompt qui ne dit pas qu'il en a.
    assert "cause d'arrêt" in prompt
    assert "chaque tâche avec son détail" in prompt
    # Et l'honnêteté, des deux côtés de la borne.
    assert "Ce qui n'y est pas, tu ne l'as pas vu" in prompt
    assert "dis-le franchement au lieu d'envoyer chercher" in prompt


# ── ③ le répondeur : ce qu'il ouvre, et sur quel objectif ─────────────────────


def _fil_approuve() -> list[MessageChat]:
    """Le fil d'un accord : la demande, la proposition du canal, le « oui »."""
    return _fil(
        "J'aimerai que tu me génère le projet p1 comme une application windows d'agenda",
        _propose(),
        "oui",
    )


SPEC_LUE = "## Sources fournies\n\n#### Source 1 — cdc.md\n\nLes fiches portent un SIRET."


def _fil_avec_une_piece_jointe() -> list[MessageChat]:
    """Le fil d'un accord dont la demande a joint une spec, lue par le fil."""
    fil = _fil_approuve()
    fil[0] = dataclasses.replace(fil[0], contexte=SPEC_LUE)
    return fil


def test_les_pieces_jointes_de_la_conversation_partent_avec_le_run() -> None:
    """#1172 : le run ouvert depuis le fil partait sans la spec dont on venait de parler."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=lanceur
    )

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_avec_une_piece_jointe()))

    assert lanceur.contextes == [SPEC_LUE]


def test_le_geste_d_accord_emporte_aussi_les_pieces_jointes() -> None:
    """Le bouton ouvre le même run que l'accord tapé, sources comprises."""
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(lanceur=lanceur)

    asyncio.run(
        repondeur.trancher_cadrage(
            AGENT_ORCHESTRATION,
            _fil_avec_une_piece_jointe(),
            approuve=True,
            objectif=OBJECTIF,
        )
    )

    assert lanceur.contextes == [SPEC_LUE]


def test_le_contexte_du_fil_ne_retient_que_ce_que_l_utilisateur_a_joint() -> None:
    """Dans l'ordre de la conversation, une seule fois chacun, jamais les réponses."""
    fil = _fil("demande", "réponse", "précision", "réponse")
    fil[0] = dataclasses.replace(fil[0], contexte="## A")
    fil[1] = dataclasses.replace(fil[1], contexte="## pas d'utilisateur")
    fil[2] = dataclasses.replace(fil[2], contexte="## A")
    fil.append(dataclasses.replace(fil[0], contenu="encore", contexte="## B"))

    assert contexte_du_fil(fil) == "## A\n\n## B"
    assert contexte_du_fil(_fil("demande sans pièce jointe")) == ""


def test_un_accord_ouvre_le_run_et_le_rattache() -> None:
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=lanceur
    )

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))

    assert lanceur.objectifs == [OBJECTIF]
    # Et la réponse porte le run : sans ce rattachement le fil dirait « c'est
    # parti » sans dire vers quoi.
    assert reponse.run_id == "run-42"
    assert "run-42" in reponse.contenu


def test_l_objectif_lance_est_celui_qui_a_ete_approuve_pas_le_message_brut() -> None:
    """Critère 3 : on ne lance pas autre chose que ce qui a été montré.

    Le dernier message est « oui » — un objectif de run qui ne veut rien dire.
    `_ouvrir_un_run` ne reçoit pas le fil, donc il ne *peut* pas le prendre : ce
    qui part est la reformulation, et rien d'autre.
    """
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=lanceur
    )

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))

    assert lanceur.objectifs == [OBJECTIF]
    assert "oui" not in lanceur.objectifs


def test_un_accord_sans_objectif_n_ouvre_rien() -> None:
    """Un verdict qui se contredit — accord sans rien à lancer — ne retombe pas sur le brut."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(_verdict(VERDICT_ACCORD, "C'est parti."), lanceur=lanceur)

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))

    assert lanceur.objectifs == []
    assert reponse.run_id == ""
    assert "pas retrouvé l'objectif" in reponse.contenu


def test_le_run_ouvert_appartient_au_projet_de_la_fenetre() -> None:
    """#683 : sans projet, un run dicté au fil n'entrait dans la vue d'aucun.

    Le fil est transverse (#281) et le reste — mais ce qu'il **ouvre** a un
    périmètre, et c'est la fenêtre qui le donne. Le projet part au lanceur avec
    l'objectif, et rien n'est deviné : le répondeur transmet, il ne cherche pas.
    """
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=lanceur
    )

    asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil_approuve(), projet_id="prj-depensio"
        )
    )

    assert lanceur.objectifs == [OBJECTIF]
    assert lanceur.projets == ["prj-depensio"]


def test_sans_projet_le_run_part_sans_projet() -> None:
    """Le comportement d'avant #683, gardé : un rattachement absent n'empêche rien.

    Le projet est une **donnée** portée par le run (#222), jamais une condition
    de son lancement — un poste sans projet actif doit continuer à ouvrir des
    runs, quitte à ce qu'ils ne relèvent d'aucune vue de projet.
    """
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=lanceur
    )

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))

    assert lanceur.projets == [None]
    assert reponse.run_id == "run-42"


def test_l_apercu_est_cadre_sur_le_projet_de_la_fenetre() -> None:
    """Juger sur l'état d'un périmètre et travailler dans un autre serait la panne de #683."""
    vus: list[str | None] = []

    def apercu(projet_id: str | None = None) -> str:
        vus.append(projet_id)
        return "Aucun run en cours."

    repondeur, _ = _repondeur(
        _verdict(VERDICT_ECHANGE, "Aucun run en cours."),
        lanceur=LanceurEspion(),
        apercu=apercu,
    )

    asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil("Où en sont les runs ?"), projet_id="prj-depensio"
        )
    )

    assert vus == ["prj-depensio"]


def test_un_lancement_en_echec_se_raconte_dans_le_fil() -> None:
    """Levée, l'exception deviendrait un 502 sans trace — or la demande est acquise."""

    async def lanceur_qui_echoue(
        objectif: str,
        projet_id: str | None = None,
        bornes: BornesRun = AUCUNE_BORNE,
        contexte_sources: str = "",
    ) -> dict[str, str]:
        raise RuntimeError("objectif refusé : plafond hors bornes")

    repondeur = RepondeurOrchestration(
        lanceur=lanceur_qui_echoue,
        provider=JugeScripte(_verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF)),
    )

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))

    assert reponse.run_id == ""
    assert "Le lancement a échoué" in reponse.contenu
    # La cause est nommée : c'est ce qui permet de reformuler.
    assert "plafond hors bornes" in reponse.contenu


class JugeEnPanne(ModelProvider):
    """Un fournisseur qui lève — réseau coupé, authentification refusée (#686)."""

    name = "panne"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise RuntimeError("fournisseur indisponible")


class JugeMuet(ModelProvider):
    """Un fournisseur qui répond, mais sans rien dire — la troisième panne (#686)."""

    name = "muet"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "   \n"


@pytest.mark.parametrize(
    ("juge", "cause"),
    [
        (JugeEnPanne(), "fournisseur indisponible"),
        (JugeMuet(), "réponse vide"),
    ],
)
def test_un_fournisseur_injoignable_se_dit_dans_le_fil_et_n_ouvre_rien(
    juge: ModelProvider, cause: str
) -> None:
    """Critère 1 : la cause est nommée, rien n'est ouvert **ni proposé**, aucun 502.

    L'invariant de #268 monté d'un cran : un empêchement se raconte dans le fil.
    Levée, l'exception deviendrait une `ReponseIndisponible` — 502 sans trace sur
    la seule porte d'entrée du produit (#666).
    """
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(lanceur=lanceur, provider=juge)

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert cause in reponse.contenu
    assert "Aucun run n'a été ouvert" in reponse.contenu
    assert "je ne vous en ai proposé aucun" in reponse.contenu
    # Passager : le geste qui répare est de renvoyer le message, qui est au fil.
    assert "renvoyez-le tel quel" in reponse.contenu
    assert reponse.run_id == ""
    assert lanceur.objectifs == []


def test_un_fournisseur_absent_est_un_reglage_et_non_une_panne_passagere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Les deux familles ne se réparent pas pareil, et la structure les sépare.

    Ce qui casse en **résolvant** le fournisseur n'a touché aucun réseau : c'est
    un réglage, et réessayer ne sert à rien. Le classement ne lit aucune chaîne —
    il tient à l'endroit de l'échec.
    """

    def sans_fournisseur() -> ModelProvider:
        raise KeyError("MAESTRO_PROVIDER='inconnu' inconnu.")

    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", sans_fournisseur
    )
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(lanceur=lanceur)

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert "réglage absent" in reponse.contenu
    assert "MAESTRO_PROVIDER" in reponse.contenu
    # La cause est déballée du `repr` que `KeyError.__str__` ajoute : l'échec de
    # configuration le plus probable serait sinon le moins lisible du fil.
    assert "\"MAESTRO_PROVIDER='inconnu' inconnu.\"" not in reponse.contenu
    assert lanceur.objectifs == []


def test_un_oui_qui_ne_peut_pas_etre_juge_ne_lance_rien_et_le_dit() -> None:
    """Le fournisseur tombe **entre** la proposition et l'accord (#686).

    Et la phrase est **la même** que sur une demande quelconque : reconnaître ce
    « oui » demanderait précisément le juge qui manque. C'est le critère 3 tenu
    par l'absence de code — aucun lexique ne reprend la main quand le modèle se
    tait.
    """
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(lanceur=lanceur, provider=JugeEnPanne())

    sur_le_oui = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))
    sur_une_demande = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert lanceur.objectifs == []
    assert sur_le_oui.run_id == ""
    assert sur_le_oui.contenu == sur_une_demande.contenu


def test_sans_lanceur_le_canal_le_dit_au_lieu_de_faire_semblant() -> None:
    """Et il le dit **avant** le « oui » : proposer ce qu'on ne peut pas ouvrir ferait attendre."""
    accord = RepondeurOrchestration(
        provider=JugeScripte(_verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF))
    )
    proposition = RepondeurOrchestration(
        provider=JugeScripte(
            _verdict(VERDICT_PROPOSITION, f"J'ouvrirais : « {OBJECTIF} ».", OBJECTIF)
        )
    )

    ouvert = asyncio.run(accord.produire(AGENT_ORCHESTRATION, _fil_approuve()))
    propose = asyncio.run(
        proposition.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"))
    )

    assert ouvert.run_id == ""
    assert "Je ne peux pas ouvrir de run" in ouvert.contenu
    assert "La demande est bien enregistrée" in ouvert.contenu
    assert "pas encore l'ouvrir" in propose.contenu


def test_repondre_rend_le_texte_de_produire() -> None:
    """`repondre` est la voie courte : le même texte, sans le rattachement."""
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=LanceurEspion()
    )

    texte = asyncio.run(repondeur.repondre(AGENT_ORCHESTRATION, _fil_approuve()))
    complet = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))

    assert texte == complet.contenu


# ── ④ le contrat SSE, vu du répondeur ─────────────────────────────────────────


@pytest.mark.parametrize(
    "reponse_du_juge",
    [
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF),
        _verdict(VERDICT_ECHANGE, "Aucun run en cours."),
    ],
)
def test_les_increments_reconstituent_exactement_la_reponse(reponse_du_juge: str) -> None:
    """Ce dont dépend un client SSE : concaténer les `delta` rend la trame `fin`.

    Éprouvé sur les **deux** voies du répondeur — celle qui ouvre un run et celle
    qui converse —, l'écriture par morceaux n'étant pas la même de part et
    d'autre. L'ébarbage est celui de `chat.Redaction` (montée dans `chat` en #693,
    où elle sert aussi le répondeur modèle), d'où la comparaison sur le texte
    ébarbé plutôt que sur la somme brute.
    """
    incremente: list[str] = []

    async def incrementer(delta: str) -> None:
        incremente.append(delta)

    repondeur, _ = _repondeur(reponse_du_juge, lanceur=LanceurEspion())

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil_approuve(), incrementer=incrementer
        )
    )

    assert incremente != []
    assert "".join(incremente).strip() == reponse.contenu


def test_sans_incrementeur_rien_n_est_publie_et_le_texte_est_le_meme() -> None:
    """`POST …/messages` passe par la même production, sans flux : elle doit tenir."""
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=LanceurEspion()
    )

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))

    assert reponse.contenu.startswith("C'est parti.")


# ── ⑤ les endpoints du fil global ─────────────────────────────────────────────


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def depot_chat(tmp_path) -> ChatStore:
    """Fil sur répertoire temporaire — jamais le `core/chat/` réel."""
    return ChatStore(tmp_path / "chat")


@pytest.fixture()
def lanceur() -> LanceurEspion:
    return LanceurEspion()


@pytest.fixture()
def juge() -> JugeScripte:
    """Le juge des tests d'endpoint : il accorde, pour que le run traverse la route."""
    return JugeScripte(_verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF))


@pytest.fixture()
def client_global(bus, depot_chat, lanceur, juge):
    """L'app avec un fil global branché sur un lanceur et un juge factices."""
    with TestClient(
        create_app(
            bus=bus,
            chat_store=depot_chat,
            orchestration_repondeur=RepondeurOrchestration(
                lanceur=lanceur,
                apercu=lambda projet_id=None: "Aucun run en cours.",
                provider=juge,
            ),
        )
    ) as client:
        yield client


def test_le_fil_global_est_servi_sans_etre_au_catalogue(client_global) -> None:
    """`orchestrateur` n'exécute aucune tâche — et son fil répond quand même.

    C'est tout le dessin du canal : la fiche est hors catalogue (rien ne doit
    pouvoir lui router une tâche), mais `/api/chat/{agent}` la résout avant de
    passer par le catalogue. Un nom inconnu, lui, reste un 404.
    """
    fil = client_global.get(f"/api/chat/{NOM_ORCHESTRATION}")

    assert fil.status_code == 200
    assert fil.json() == {
        "agent": NOM_ORCHESTRATION,
        "role": "Orchestrateur",
        # Le fil global a des conversations comme les autres (#694) : sans
        # précision on lit la plus récente, et un fil neuf n'a que son `origine`.
        "conversation": CONVERSATION_ORIGINE,
        "messages": [],
    }
    assert NOM_ORCHESTRATION not in {
        agent["nom"] for agent in client_global.get("/api/catalogue").json()
    }
    assert client_global.get("/api/chat/pas-un-agent").status_code == 404


def test_un_accord_poste_au_fil_global_ouvre_un_run_et_le_porte(
    client_global, lanceur, depot_chat
) -> None:
    reponse = client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": "oui"}
    )

    assert reponse.status_code == 201
    envoye, repondu = reponse.json()["messages"]
    assert envoye["auteur"] == UTILISATEUR and envoye["run_id"] == ""
    # Le `run_id` voyage jusqu'au JSON du message persisté : c'est lui que
    # l'écran relit pour lister ce que le fil a ouvert (#269).
    assert repondu["run_id"] == "run-42"
    assert lanceur.objectifs == [OBJECTIF]
    # Le fil global a son propre fichier, sous le nom du canal.
    assert (depot_chat.racine / f"{NOM_ORCHESTRATION}.jsonl").is_file()


def test_le_run_ouvert_part_aussi_sur_le_websocket(client_global) -> None:
    """Un client temps réel apprend le rattachement sans rien relire."""
    with client_global.websocket_connect("/ws/evenements?projet=tous") as ws:
        client_global.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": "vas-y"}
        )
        aller = ws.receive_json()
        retour = ws.receive_json()

    assert aller["type"] == EVENEMENT_CHAT_MESSAGE and aller["run_id"] == ""
    assert retour["type"] == EVENEMENT_CHAT_MESSAGE
    assert retour["agent"] == NOM_ORCHESTRATION and retour["run_id"] == "run-42"


def test_une_demande_postee_au_fil_global_n_ouvre_rien(bus, depot_chat, lanceur) -> None:
    """La route ne court-circuite pas la règle : une proposition n'ouvre aucun run."""
    with TestClient(
        create_app(
            bus=bus,
            chat_store=depot_chat,
            orchestration_repondeur=RepondeurOrchestration(
                lanceur=lanceur,
                provider=JugeScripte(
                    _verdict(VERDICT_PROPOSITION, f"J'ouvrirais : « {OBJECTIF} ».", OBJECTIF)
                ),
            ),
        )
    ) as client:
        reponse = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages",
            json={"contenu": "Génère une application d'agenda"},
        )

    _, repondu = reponse.json()["messages"]
    assert repondu["run_id"] == ""
    assert lanceur.objectifs == []


def test_un_fournisseur_injoignable_laisse_la_demande_acquise(
    bus, depot_chat, lanceur
) -> None:
    """Critère 2 : l'indisponibilité concerne la réponse, jamais la demande (#686).

    Le message d'utilisateur est persisté **et** diffusé avant que le répondeur ne
    soit appelé : il reste au fil, son auteur relance sans retaper. Et la route
    rend 201 avec la phrase franche, là où l'exception donnait un 502 muet — ce
    n'est pas qu'un code, c'est la différence entre un fil qui explique et un fil
    où rien ne revient.
    """
    with TestClient(
        create_app(
            bus=bus,
            chat_store=depot_chat,
            orchestration_repondeur=RepondeurOrchestration(
                lanceur=lanceur, provider=JugeEnPanne()
            ),
        )
    ) as client:
        with client.websocket_connect("/ws/evenements?projet=tous") as ws:
            reponse = client.post(
                f"/api/chat/{NOM_ORCHESTRATION}/messages",
                json={"contenu": "Génère une application d'agenda"},
            )
            aller = ws.receive_json()
            retour = ws.receive_json()

    assert reponse.status_code == 201
    envoye, repondu = reponse.json()["messages"]
    assert envoye["contenu"] == "Génère une application d'agenda"
    assert "fournisseur indisponible" in repondu["contenu"]
    assert repondu["run_id"] == "" and lanceur.objectifs == []
    # Les deux messages sont au fil persisté, et les deux sont partis sur le bus.
    assert [message.auteur for message in depot_chat.fil(NOM_ORCHESTRATION)] == [
        UTILISATEUR,
        NOM_ORCHESTRATION,
    ]
    assert aller["type"] == EVENEMENT_CHAT_MESSAGE
    assert retour["type"] == EVENEMENT_CHAT_MESSAGE


def _trames(reponse) -> list[dict]:
    """Les objets JSON d'un corps `text/event-stream` (`data: <json>` par trame)."""
    return [
        json.loads(ligne[len("data: ") :])
        for ligne in reponse.text.splitlines()
        if ligne.startswith("data: ")
    ]


def test_le_flux_du_fil_global_rend_debut_fragments_et_fin(client_global) -> None:
    """Le canal SSE vaut pour les trois fils — ici le global, qui agit en plus."""
    reponse = client_global.get(
        f"/api/chat/{NOM_ORCHESTRATION}/flux", params={"contenu": "oui"}
    )

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("text/event-stream")
    trames = _trames(reponse)
    assert trames[0]["type"] == FRAGMENT_CHAT_DEBUT
    assert trames[-1]["type"] == FRAGMENT_CHAT_FIN
    deltas = [t["delta"] for t in trames if t["type"] == FRAGMENT_CHAT_DELTA]
    assert deltas != []
    # La promesse du contrat : les `delta` seuls reconstituent la trame `fin`.
    final = trames[-1]["message"]
    assert "".join(deltas).strip() == final["contenu"]
    assert final["run_id"] == "run-42"


def test_un_contenu_vide_sort_en_422_sans_rien_persister(client_global, depot_chat) -> None:
    """La question se tranche **avant** la première trame — sinon plus de statut à rendre."""
    reponse = client_global.get(f"/api/chat/{NOM_ORCHESTRATION}/flux", params={"contenu": ""})

    assert reponse.status_code == 422
    assert client_global.get(f"/api/chat/{NOM_ORCHESTRATION}").json()["messages"] == []
    assert not (depot_chat.racine / f"{NOM_ORCHESTRATION}.jsonl").exists()


# ── ⑥ le projet de la fenêtre, du corps de la requête jusqu'au run (#683) ─────


def test_le_projet_de_la_fenetre_voyage_du_corps_jusqu_au_lanceur(
    client_global, lanceur
) -> None:
    """Le rattachement traverse la route sans rien perdre en chemin."""
    client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages",
        json={"contenu": "oui", "projet_id": "prj-depensio"},
    )

    assert lanceur.projets == ["prj-depensio"]


def test_un_projet_mal_forme_vaut_aucun_projet(client_global, lanceur) -> None:
    """Normalisé à la frontière (#222) : un identifiant douteux ne fait pas échouer un message.

    Le rattachement est une donnée, pas une condition du lancement. Refuser le
    message ferait dépendre une conversation de la bonne tenue d'un identifiant
    que l'utilisateur n'a jamais tapé.
    """
    reponse = client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages",
        json={"contenu": "oui", "projet_id": "../../etc"},
    )

    assert reponse.status_code == 201
    assert lanceur.projets == [None]


def test_le_flux_porte_le_projet_comme_le_post(client_global, lanceur) -> None:
    """Les deux voies mènent au même `_repondre` : un run ouvert par le flux se rattache aussi.

    `?projet_id=` et non `?projet=` : ce dernier désigne partout ailleurs une
    **portée** de lecture, avec ses mots réservés `tous`/`aucun` (#277), et deux
    contrats sous un même nom seraient la première façon de les confondre.
    """
    client_global.get(
        f"/api/chat/{NOM_ORCHESTRATION}/flux",
        params={"contenu": "oui", "projet_id": "prj-depensio"},
    )

    assert lanceur.projets == ["prj-depensio"]


def test_le_fil_lui_meme_reste_transverse(client_global, depot_chat) -> None:
    """Le projet accompagne la demande ; il n'entre ni dans le fil ni dans l'événement.

    C'est la moitié de #281 que ce lot **ne** défait pas : un `chat.message` sans
    `projet_id` est ce qui permet à une socket cadrée sur un projet de recevoir
    quand même le fil. Le rattachement vit sur le **run** ouvert, jamais sur le
    message qui l'a demandé.
    """
    with client_global.websocket_connect("/ws/evenements?projet=tous") as ws:
        client_global.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages",
            json={"contenu": "oui", "projet_id": "prj-depensio"},
        )
        aller = ws.receive_json()
        retour = ws.receive_json()

    assert aller["projet_id"] is None and retour["projet_id"] is None
    persistes = depot_chat.fil(NOM_ORCHESTRATION)
    assert [m.to_dict().get("projet_id") for m in persistes] == [None, None]


# ── ⑦ le câblage réel : de la demande au run qui figure dans la liste (#683) ──
#
# Les tests ci-dessus injectent le répondeur, donc **court-circuitent** le
# lanceur que l'app construit — or c'est précisément lui qui était en défaut :
# `ouvrir_un_run` appelait `lancer(objectif)` sans projet, et le run naissait
# orphelin. Ceux-ci montent donc l'app **sans** `orchestration_repondeur`, avec
# un moteur muet et deux projets réellement déclarés, et lisent le résultat par
# la route que l'écran interroge. Le fournisseur, lui, est substitué à la
# fabrique : c'est le seul maillon qu'on ne peut pas laisser réel (#195).


class MoteurMuet:
    """Moteur injecté à la place du vrai : il n'appelle rien et note son projet.

    Volontairement plus court que le `MoteurDouble` de `tests/test_executions.py`
    (dont ce n'est pas le sujet ici) : ce qui compte est que `projet_id` arrive
    jusqu'au moteur, donc jusqu'aux tâches du plan. Il ne peut pas rendre un faux
    vert — un service qui passerait le projet sous un autre nom lui ferait noter
    `None`, et l'assertion tomberait.
    """

    def __init__(self) -> None:
        self.projets: list[str | None] = []
        self.objectifs: list[str] = []
        #: Les garde-fous reçus à la fabrication (#990), dans l'ordre du contrat
        #: de `lancer` : coût, tokens, délai par tâche, parallélisme. C'est ici
        #: qu'ils arrivent — `ServiceExecutions` les passe en `guardrails` et
        #: `max_parallele` — et c'est donc ici qu'on voit si le chat les a
        #: réellement transmis, ou s'il a seulement écrit la bonne phrase.
        self.garde_fous: list[tuple[float | None, int | None, float | None, int | None]] = []

    def __call__(self, **reglages: Any) -> MoteurMuet:
        gardes = reglages.get("guardrails")
        self.garde_fous.append(
            (
                getattr(gardes, "plafond_cout_usd", None),
                getattr(gardes, "plafond_tokens", None),
                getattr(gardes, "timeout_s", None),
                reglages.get("max_parallele"),
            )
        )
        return self

    async def run(self, objectif: str, *, projet_id: str | None = None, **reste: Any) -> RunReport:
        self.projets.append(projet_id)
        self.objectifs.append(objectif)
        return RunReport(objectif=objectif, resultats=())


@pytest.fixture()
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — même raison qu'en #221/#223.

    Sous Windows le `tmp_path` de pytest vit dans `AppData/Local/Temp`, que la
    validation de racine refuse à raison : sans cette isolation, déclarer un
    projet échouerait pour une bonne raison, mais pas celle qu'on mesure ici.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def projets(maison: Path, tmp_path: Path) -> ServiceProjets:
    """Deux projets réellement **déclarés** : la portée n'accepte qu'eux (#277)."""
    service = ServiceProjets(ProjetStore(tmp_path / "depot"))
    for nom in ("depensio", "autre"):
        (maison / nom).mkdir()
        service.creer(nom, str(maison / nom))
    return service


@pytest.fixture()
def moteur() -> MoteurMuet:
    return MoteurMuet()


@pytest.fixture()
def fournisseur_par_defaut(monkeypatch: pytest.MonkeyPatch) -> JugeScripte:
    """Le juge que `RepondeurOrchestration` résoudra tout seul (#195 : aucun réseau).

    Substitué sur la **fabrique** et non sur le répondeur, puisque c'est le
    répondeur construit par `create_app` qu'on veut exercer ici — celui-là même
    qui tient le lanceur réel de l'app.
    """
    juge = JugeScripte(_verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF))
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: juge
    )
    return juge


@pytest.fixture()
def agents_equipes(tmp_path: Path, projets: ServiceProjets) -> AgentStore:
    """Un dépôt d'agents jetable où **chaque projet déclaré a son équipe**.

    Depuis #1146 le fil ne propose plus de run à un projet sans agent : il lui
    propose son équipe. Les tests de cette section parlent du rattachement d'un
    run à son projet, pas du recrutement — leurs projets ont donc quelqu'un pour
    prendre les tâches, comme en vrai après l'étape d'équipe.
    """
    store = AgentStore(tmp_path / "agents")
    for projet in projets.lister():
        store.pour_projet(projet["id"]).ecrire(
            definition(
                RoleValide(
                    nom="dev-projet",
                    role="Développeur",
                    competences=("backend",),
                    playbook="Tu écris le code.",
                )
            )
        )
    return store


@pytest.fixture()
def client_reel(bus, depot_chat, projets, moteur, fournisseur_par_defaut, agents_equipes):
    """L'app **entière** : vrai répondeur d'orchestration, vrai service d'exécutions."""
    with TestClient(
        create_app(
            bus=bus,
            state=ControlTowerState(),
            chat_store=depot_chat,
            projets=projets,
            agents_store=agents_equipes,
            fabrique_moteur=moteur,
        )
    ) as client:
        yield client


def _demander(client: TestClient, **corps: Any) -> str:
    """Approuve une proposition dans le fil et rend le `run_id` que la réponse porte."""
    reponse = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": "oui", **corps}
    )
    assert reponse.status_code == 201
    return reponse.json()["messages"][1]["run_id"]


def _runs_de(client: TestClient, portee: str) -> set[str]:
    """Les runs que la liste de l'écran rend pour cette portée (`GET /api/executions`)."""
    reponse = client.get("/api/executions", params={"projet": portee})
    assert reponse.status_code == 200
    return {run["run_id"] for run in reponse.json()}


def test_un_run_dicte_au_fil_figure_dans_la_liste_de_son_projet(
    client_reel, projets, moteur
) -> None:
    """Le défaut de #683, par le chemin exact où il se produisait.

    Le run ouvert depuis le fil appartient au projet de la fenêtre : il figure
    dans la liste des runs de ce projet — celle que l'écran lit —, il ne figure
    pas dans celle du projet d'à côté, et son projet descend jusqu'au moteur,
    donc jusqu'aux tâches du plan (#222).
    """
    ici, ailleurs = (p["id"] for p in projets.lister())

    run_id = _demander(client_reel, projet_id=ici)

    assert run_id in _runs_de(client_reel, ici)
    assert run_id not in _runs_de(client_reel, ailleurs)
    assert run_id not in _runs_de(client_reel, "aucun")
    # Ce que le moteur a réellement reçu — le projet (#683) et, depuis #685,
    # l'objectif **approuvé** plutôt que le « oui » qui l'a approuvé. Les deux
    # s'assertent ici parce que c'est la même question : ce qui est descendu
    # jusqu'à la décomposition, une fois toute la chaîne traversée.
    assert moteur.projets == [ici]
    assert moteur.objectifs == [OBJECTIF]


def test_sans_projet_le_run_reste_introuvable_a_l_ecran(client_reel, projets) -> None:
    """L'échantillon fautif : ce que faisait **tout** run du chat avant #683.

    Sans rattachement, le run n'entre dans la vue d'aucun projet (`PorteeProjet`,
    #277) — il n'est atteignable que sous `aucun`, portée qu'aucun sélecteur de
    l'UI ne propose. C'est ce qui le rendait invisible dans la liste et
    impossible à ouvrir en détail, alors même que l'orchestrateur l'annonçait en
    cours. Le garder ici est ce qui empêche de croire que l'assertion précédente
    passerait de toute façon.
    """
    ici, _ = (p["id"] for p in projets.lister())

    run_id = _demander(client_reel)

    assert run_id not in _runs_de(client_reel, ici)
    assert run_id in _runs_de(client_reel, "aucun")


# ── ⑧ le protocole d'accord, joué de bout en bout (#688) ──────────────────────
#
# Les tests d'au-dessus scriptent **un** verdict et regardent ce qui en sort ;
# ceux-ci jouent les **deux tours** sur le même répondeur, parce que c'est là que
# vit la décision du 2026-08-28 : proposer et lancer sont deux messages, et rien
# entre les deux ne tient d'état. Un test à verdict unique ne peut pas le dire —
# il ne voit jamais l'intervalle où la panne se logerait.


class JugeEnSequence(ModelProvider):
    """Un fournisseur qui rend une réponse **différente à chaque appel** (#688).

    C'est ce qui manquait pour éprouver le protocole : `JugeScripte` rend toujours
    le même verdict, donc ne peut pas jouer « je propose, puis j'accorde ». Il
    garde les prompts pour qu'on puisse vérifier ce que le second tour a vu — le
    fil étant la **seule** mémoire du canal, la proposition doit y être.

    Épuisé, il **lève** au lieu de répéter la dernière réponse : un tour de trop
    est un test qui ne dit plus ce qu'il croit dire, et le silencieux serait de
    rejouer un accord.
    """

    name = "juge-en-sequence"

    def __init__(self, *reponses: str) -> None:
        self._reponses = list(reponses)
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        self.prompts.append(prompt)
        if not self._reponses:
            raise AssertionError("le juge a été appelé plus de fois que prévu")
        return self._reponses.pop(0)


#: Le « oui » tel qu'un utilisateur l'écrit — assez long pour qu'on voie, à
#: l'assertion, qu'il n'est **pas** ce qui part au lanceur. Un « oui » nu se
#: confondrait avec une troncature ; celui-ci ne peut se confondre avec rien.
ACCORD_ECRIT = "oui vas-y, fonce"


def test_la_proposition_puis_l_accord_n_ouvrent_qu_au_second_tour() -> None:
    """Critère 2, joué en entier : proposition → rien, puis accord → run (#688).

    Le même répondeur, deux messages, un fil qui grandit entre les deux. C'est
    l'invariant « aucun run ne s'ouvre sans accord explicite » sous sa seule forme
    complète : après le premier tour le lanceur est **intact**, et c'est le second
    message — pas le premier, pas le temps qui passe — qui ouvre.
    """
    lanceur = LanceurEspion()
    juge = JugeEnSequence(
        _verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF),
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF),
    )
    repondeur = RepondeurOrchestration(lanceur=lanceur, provider=juge)
    demande = "J'aimerai que tu me génère le projet p1 comme une application d'agenda"

    propose = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil(demande)))
    # Le premier tour n'a rien ouvert : c'est le sujet même du chantier.
    assert lanceur.objectifs == []
    assert propose.run_id == ""

    ouvert = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil(demande, _propose(), ACCORD_ECRIT)
        )
    )

    assert lanceur.objectifs == [OBJECTIF]
    assert ouvert.run_id == "run-42"
    # Le fil est la seule mémoire : le second appel a bien reçu la proposition du
    # premier. Sans elle, juger « oui vas-y » demanderait un second lexique.
    assert _propose() in juge.prompts[1]


@pytest.mark.parametrize(
    ("verdict_du_second_tour", "suite"),
    [
        pytest.param(
            _verdict(VERDICT_ECHANGE, "Entendu, je n'ouvre rien."),
            "plutôt pas, finalement",
            id="refus",
        ),
        pytest.param(
            _verdict(VERDICT_ECHANGE, "Aucun run en cours."),
            "au fait, où en sont les runs ?",
            id="on-parle-d-autre-chose",
        ),
    ],
)
def test_apres_une_proposition_tout_ce_qui_n_est_pas_un_accord_n_ouvre_rien(
    verdict_du_second_tour: str, suite: str
) -> None:
    """Critère 2 : seul l'accord ouvre — un refus comme un changement de sujet ne font rien.

    Le second cas est le plus utile des deux : il montre qu'une proposition ne
    reste pas « en attente » derrière le fil, prête à être ramassée par le message
    suivant quel qu'il soit. Le run n'est ouvert que sur le verdict `accord` d'un
    message qui arrive, jamais sur une intention qu'on aurait mise de côté.
    """
    lanceur = LanceurEspion()
    juge = JugeEnSequence(
        _verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF), verdict_du_second_tour
    )
    repondeur = RepondeurOrchestration(lanceur=lanceur, provider=juge)

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination")))
    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil("Ajoute la pagination", _propose(), suite)
        )
    )

    assert lanceur.objectifs == []
    assert reponse.run_id == ""


def test_le_silence_n_est_pas_un_accord() -> None:
    """Critère 2 : une proposition sans réponse n'ouvre rien, et ne laisse rien derrière.

    Le silence n'est pas un message, donc aucun verdict n'est rendu, donc rien ne
    s'ouvre — la propriété est **structurelle** et c'est ce que ce test montre
    plutôt que de laisser passer le temps : après la proposition, le juge n'a été
    appelé qu'une fois et le répondeur ne garde aucune trace de l'objectif qu'il
    vient de proposer. Sans cette seconde assertion le test serait une tautologie
    (« on n'a rien appelé, donc rien ne s'est passé ») ; avec elle il interdit le
    correctif le plus tentant — mémoriser la dernière proposition pour la
    ramasser plus tard, qui ferait du silence un accord différé.
    """
    lanceur = LanceurEspion()
    juge = JugeEnSequence(_verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF))
    repondeur = RepondeurOrchestration(lanceur=lanceur, provider=juge)

    asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Génère une application d'agenda"))
    )

    assert lanceur.objectifs == []
    assert len(juge.prompts) == 1
    # Aucun état de session : le répondeur porte **exactement** les
    # collaborateurs qu'on lui a passés, et pas un attribut de plus où loger une
    # proposition en attente. C'est cette forme-là qu'on garde plutôt qu'une
    # recherche de l'objectif dans `vars()` — un objectif rangé dans un objet
    # imbriqué y échapperait, alors qu'un attribut nouveau, lui, se voit toujours.
    #
    # ⚠ #1031 en a ajouté un quatrième, `_conducteur` (le questionnaire
    # d'outillage), et la faille que le commentaire ci-dessus nomme devenait donc
    # réelle : un collaborateur est précisément l'« objet imbriqué » où une
    # proposition pourrait se loger. La garantie est donc **poussée d'un cran**
    # sur lui plutôt qu'élargie — le conducteur ne porte **aucun** attribut, son
    # état étant le fil qu'on lui passe à chaque appel
    # (`maestro.controltower.outillage`).
    #
    # #1173 en ajoute deux, et aucun n'est un état de session. `_sonde` est un
    # collaborateur injecté (la sonde du poste, appelée seulement quand aucun
    # fournisseur n'est réglé). `_modele` est le modèle **résolu avec le
    # fournisseur**, posé une fois depuis la configuration et jamais par un
    # message : il reste `None` avec un fournisseur injecté, ce que ce test prouve.
    #
    # #1146 en ajoute deux, injectés eux aussi et sans état : `_equipe` (la sonde
    # qui compte les agents du projet) et `_recruteur` (la création d'équipe de
    # #1040). L'équipe proposée, elle, ne loge **pas** dans le répondeur : elle
    # voyage sur le message (`MessageChat.recrutement`), et c'est le fil qui la
    # rend au geste — la même règle que la proposition de run.
    #
    # #1157 en ajoute un, `_faits` : la lecture des runs, injectée comme l'aperçu
    # et relue **à chaque question**. Elle ne retient donc rien d'un tour à
    # l'autre, ce qui est exactement ce que ce test protège — un répondeur qui
    # mémoriserait un run entre deux messages rouvrirait la porte que #685 a
    # fermée.
    assert set(vars(repondeur)) == {
        "_lanceur",
        "_apercu",
        "_faits",
        "_provider",
        "_modele",
        "_conducteur",
        "_sonde",
        "_equipe",
        "_recruteur",
    }
    assert repondeur._equipe is None and repondeur._recruteur is None
    assert vars(repondeur._conducteur) == {}
    assert repondeur._modele is None


def test_l_objectif_lance_est_celui_qui_a_ete_montre_pas_ce_que_le_fil_contient() -> None:
    """Critère 2, dernière moitié : on lance ce qui a été **montré et approuvé**.

    Le fil porte trois textes qui pourraient tous passer pour un objectif — la
    demande d'origine, la proposition, le « oui vas-y, fonce ». Un seul part, et
    c'est celui que le modèle a recopié de sa proposition. `_ouvrir_un_run` ne
    reçoit pas le fil, donc la garantie tient à la **forme du code** et non à une
    vérification qu'il faudrait tenir à jour.
    """
    lanceur = LanceurEspion()
    demande = "J'aimerai que tu me génère le projet p1"
    juge = JugeEnSequence(
        _verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF),
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF),
    )
    repondeur = RepondeurOrchestration(lanceur=lanceur, provider=juge)

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil(demande)))
    asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil(demande, _propose(), ACCORD_ECRIT)
        )
    )

    assert lanceur.objectifs == [OBJECTIF]
    # Aucun des textes du fil n'a pu partir comme objectif de run.
    for texte in (demande, _propose(), ACCORD_ECRIT):
        assert texte not in lanceur.objectifs


# ── ⑨ le juge est jouable sans fournisseur (#688, règle de tests/conftest.py) ──


def test_construire_le_canal_ne_resout_aucun_fournisseur(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Critère 3 : ni réseau ni authentification pour **monter** le canal (#195).

    La résolution est paresseuse — c'est ce dont `create_app` dépend : une Control
    Tower doit démarrer sur un poste sans clé, et n'échouer qu'au message qui
    demande un jugement. Le test prouve son motif en deux temps plutôt qu'en
    affirmant l'absence d'appel : la fabrique est comptée, elle est à **zéro** à
    la construction, et à **un** dès le premier message — sans cette seconde
    moitié, une sonde mal branchée rendrait un ✓ sur une question jamais posée.
    """
    appels: list[int] = []

    def fabrique_comptee() -> ModelProvider:
        appels.append(1)
        raise RuntimeError("aucun fournisseur configuré")

    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", fabrique_comptee
    )

    repondeur = RepondeurOrchestration(lanceur=LanceurEspion())

    assert appels == []

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination")))

    assert appels == [1]


def test_le_point_d_injection_dispense_l_app_de_tout_fournisseur(
    bus, depot_chat, lanceur, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critère 3 : `orchestration_repondeur` est ce par quoi la suite juge sans modèle.

    Toute la couverture du canal passe par lui — c'est ce qui permet à
    `tests/conftest.py` d'exiger qu'aucun test n'ait besoin d'un backend. La
    fabrique est ici **piégée** : elle fait rougir le test si quoi que ce soit,
    du montage de l'app jusqu'au run ouvert, tente de résoudre un fournisseur.
    """

    def fabrique_interdite() -> ModelProvider:
        raise AssertionError("un fournisseur a été résolu : la suite sort du bocal")

    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", fabrique_interdite
    )

    with TestClient(
        create_app(
            bus=bus,
            chat_store=depot_chat,
            orchestration_repondeur=RepondeurOrchestration(
                lanceur=lanceur,
                provider=JugeScripte(_verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF)),
            ),
        )
    ) as client:
        reponse = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": ACCORD_ECRIT}
        )

    assert reponse.status_code == 201
    assert lanceur.objectifs == [OBJECTIF]


# ── ⑩ le lexique est parti, et rien ne le fait revenir (#688) ─────────────────
#
# La moitié **comportementale** de ce critère vit plus haut
# (`test_le_canal_ne_tranche_plus_avant_le_juge` : le juge est appelé sur chacune
# des cinq formulations, donc aucune voie rapide ne tranche avant lui). Ce qui
# suit en est la moitié **structurelle** : les symboles retirés ne sont ni
# définis ni référencés nulle part, y compris comme repli.

#: Les noms du lexique retiré en #685. Ils sont assez distinctifs pour être
#: cherchés dans tout le dépôt sans risque de collision — à la différence de
#: `intention`, mot français courant dont le dépôt parle légitimement (le brief
#: du Chef de projet « reformule l'intention »), et qui n'est donc cherché que
#: dans le module et sa suite.
LEXIQUE_RETIRE = ("_AMORCES", "_VERBES_TRAVAIL", "_sans_amorce")
LEXIQUE_RETIRE_LOCAL = LEXIQUE_RETIRE + ("intention", "INTENTION_TRAVAIL", "INTENTION_ECHANGE")

#: `amorcesDuProjet` / `AMORCES_ASSISTANCE` (côté TypeScript) ne sont **pas**
#: ce lexique : ce sont les amorces de conversation proposées sur un fil vide. Le
#: motif porte sur des identifiants **Python**, ce qui rend la confusion
#: impossible — et c'est pourquoi il passe par l'arbre syntaxique plutôt que par
#: un `grep`, qui les aurait ramassées toutes les deux.
RACINE_DEPOT = Path(__file__).resolve().parents[1]


def _identifiants_python(source: str) -> set[str]:
    """Les noms **effectivement écrits en code** dans `source` (jamais en prose).

    Un `grep` ne distingue pas un usage d'une mention, or ce module *doit* citer
    le lexique pour raconter pourquoi il a été retiré — la garde le condamnerait
    sur la docstring même qui le documente. L'arbre syntaxique tranche : un nom
    cité dans une chaîne ou un commentaire n'y est pas un identifiant.
    """
    arbre = ast.parse(source)
    noms: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Name):
            noms.add(noeud.id)
        elif isinstance(noeud, ast.Attribute):
            noms.add(noeud.attr)
        elif isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            noms.add(noeud.name)
        elif isinstance(noeud, ast.arg):
            noms.add(noeud.arg)
    return noms


def test_le_motif_du_lexique_reconnait_un_echantillon_fautif() -> None:
    """Avant de balayer : la sonde attrape-t-elle ce qu'elle prétend chercher ?

    Sans cette preuve, un motif mal écrit rendrait un ✓ vert sur tout le dépôt en
    ne cherchant rien (règle de `tests/test_cycle_de_vie.py`). L'échantillon porte
    les trois formes par lesquelles le lexique reviendrait : une constante, une
    fonction, un appel — et, en regard, la **mention** en docstring, qui elle doit
    passer.
    """
    fautif = _identifiants_python(
        '"""On parle de `_VERBES_TRAVAIL` dans cette prose, et de _AMORCES aussi."""\n'
        "_VERBES_TRAVAIL = ['ajoute', 'cree']\n"
        "def _sans_amorce(message):\n"
        "    return intention(message)\n"
    )

    assert {"_VERBES_TRAVAIL", "_sans_amorce", "intention"} <= fautif
    # Et la moitié qui sépare l'usage de la mention : `_AMORCES` n'est ici que
    # cité dans la docstring, donc la sonde ne le voit pas.
    assert "_AMORCES" not in fautif


def test_aucune_trace_du_lexique_ne_subsiste_dans_le_module_ni_sa_suite() -> None:
    """Critère de doc 3 : ni juge, ni voie rapide, ni repli (#682/#685).

    Le module et sa suite sont regardés de près — c'est là que le lexique
    reviendrait, et c'est là que le mot `intention` serait le signe qu'il est
    revenu. Ailleurs, le mot est légitime et n'est pas cherché.
    """
    for chemin in (
        RACINE_DEPOT / "maestro" / "controltower" / "orchestration.py",
        RACINE_DEPOT / "tests" / "test_chat_global.py",
    ):
        ecrits = _identifiants_python(chemin.read_text(encoding="utf-8"))
        survivants = sorted(set(LEXIQUE_RETIRE_LOCAL) & ecrits)
        assert survivants == [], f"{chemin.name} porte encore {survivants}"


def test_le_lexique_n_a_pas_non_plus_reparu_ailleurs_dans_le_depot() -> None:
    """Le repli se poserait volontiers **à côté** du module, dans un helper à lui.

    D'où le balayage de tout le Python du dépôt sur les trois noms distinctifs :
    une « voie rapide » extraite dans `maestro/controltower/lexique.py` passerait
    la garde d'au-dessus sans être vue. Les mentions en prose sont invisibles à
    l'arbre syntaxique, donc ce fichier-ci et la docstring du module — qui
    doivent raconter le retrait — ne se condamnent pas eux-mêmes.
    """
    fautifs: list[str] = []
    balayes: list[str] = []
    for chemin in sorted(RACINE_DEPOT.glob("maestro/**/*.py")) + sorted(
        RACINE_DEPOT.glob("tests/**/*.py")
    ):
        balayes.append(chemin.relative_to(RACINE_DEPOT).as_posix())
        ecrits = _identifiants_python(chemin.read_text(encoding="utf-8"))
        for nom in sorted(set(LEXIQUE_RETIRE) & ecrits):
            fautifs.append(f"{chemin.relative_to(RACINE_DEPOT).as_posix()} : {nom}")

    # Le balayage a bien eu lieu : un glob qui ne ramènerait rien rendrait ce
    # test vert sans avoir rien regardé — c'est le ✓ sur une question jamais
    # posée que la maison refuse. Le module visé en fait nommément partie.
    assert "maestro/controltower/orchestration.py" in balayes
    assert len(balayes) > 50
    assert fautifs == []


def test_le_module_n_expose_aucun_juge_lexical() -> None:
    """La surface publique le dit aussi : plus rien à appeler pour « classer » un texte.

    `dir()` plutôt que la source : c'est ce qu'un appelant peut atteindre, donc ce
    qu'un repli irait chercher. Les trois verdicts, eux, sont **là** — sans quoi
    ce test passerait sur un module vide (le vert d'une question jamais posée).
    """
    from maestro.controltower import orchestration

    surface = set(dir(orchestration))

    assert surface & set(LEXIQUE_RETIRE_LOCAL) == set()
    assert {"VERDICT_PROPOSITION", "VERDICT_ACCORD", "VERDICT_ECHANGE"} <= surface


# ── ⑨ la demande de cadrage sort de la phrase, et se répond d'un geste (#943) ─
#
# Constat **G10** du retex du 2026-09-11 : l'orchestration écrit « Je lance ? »
# sans bouton, et le panneau « Cadrage en attente » dit « aucun » au même
# instant. Le défaut n'était pas l'absence d'un bouton : la demande n'existait
# **que** dans le texte de la réponse, donc aucune surface ne pouvait la voir.


def _repondeur_qui_propose(
    lanceur: LanceurEspion | None = None,
) -> RepondeurOrchestration:
    """Un répondeur dont le juge propose — l'état de départ de tous ces tests."""
    return RepondeurOrchestration(
        lanceur=lanceur,
        provider=JugeScripte(_verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF)),
    )


def test_une_proposition_porte_l_objectif_jusqu_au_message(lanceur) -> None:
    """La demande quitte la phrase : elle est sur le message, donc lisible par un écran."""
    reponse = asyncio.run(
        _repondeur_qui_propose(lanceur).produire(
            AGENT_ORCHESTRATION, _fil("Génère une application d'agenda")
        )
    )

    assert reponse.proposition == OBJECTIF
    # Et rien n'est ouvert pour autant : proposer n'est pas lancer (#685).
    assert reponse.run_id == "" and lanceur.objectifs == []


def test_un_echange_ne_porte_aucune_demande() -> None:
    """Pas de geste sur une réponse qui ne demande rien — sinon le bouton serait partout."""
    repondeur, _ = _repondeur(_verdict(VERDICT_ECHANGE, "Trois runs tournent."))

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("où en sont les runs ?"))
    )

    assert reponse.proposition == ""


def test_sans_lanceur_une_proposition_n_offre_aucun_geste() -> None:
    """Le message vient de dire que le run n'ouvrirait pas : offrir le bouton le contredirait.

    C'est la seule raison pour laquelle une proposition peut ne pas porter sa
    demande — et elle est mécanique, pas cosmétique : le geste mènerait à un
    lancement que le canal annonce impossible une ligne plus haut.
    """
    repondeur = RepondeurOrchestration(
        lanceur=None,
        provider=JugeScripte(_verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF)),
    )

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Génère une application d'agenda"))
    )

    assert "pas encore l'ouvrir" in reponse.contenu
    assert reponse.proposition == ""


def test_une_demande_attend_tant_que_rien_n_a_suivi() -> None:
    """Le dernier message, et lui seul : ce n'est pas le temps qui périme une demande.

    Les deux moitiés comptent. Sans la première, aucune surface ne verrait la
    demande ; sans la seconde, le geste resterait offert après qu'on a répondu
    — et un second clic ouvrirait un run de plus.
    """
    demande = MessageChat(
        agent=NOM_ORCHESTRATION,
        auteur=NOM_ORCHESTRATION,
        contenu=_propose(),
        proposition=OBJECTIF,
    )
    suite = MessageChat(
        agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="plutôt pas"
    )

    assert proposition_en_attente([]) is None
    assert proposition_en_attente(_fil("bonjour")) is None
    assert proposition_en_attente([demande]) is demande
    assert proposition_en_attente([demande, suite]) is None


def test_un_repondeur_qui_ne_propose_rien_n_a_rien_a_trancher() -> None:
    """Le défaut de la classe de base : il le dit au lieu de le laisser deviner."""
    with pytest.raises(CadrageIntrouvable):
        asyncio.run(
            RepondeurScripte().trancher_cadrage(
                AGENT_ORCHESTRATION, [], approuve=True, objectif=OBJECTIF
            )
        )


@pytest.fixture()
def client_proposition(bus, depot_chat, lanceur):
    """L'app dont le juge **propose** : l'écran a donc une demande à trancher.

    Le juge est laissé sur `proposition` pour toute la durée du test, y compris
    après le geste : c'est ce qui rend visible qu'un accord au bouton ne repasse
    pas devant lui — s'il y repassait, il rendrait une proposition de plus et
    aucun run ne partirait.
    """
    with TestClient(
        create_app(
            bus=bus,
            chat_store=depot_chat,
            orchestration_repondeur=_repondeur_qui_propose(lanceur),
        )
    ) as client:
        client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages",
            json={"contenu": "Génère une application d'agenda"},
        )
        yield client


def test_la_demande_est_servie_au_fil_donc_visible_par_les_deux_surfaces(
    client_proposition,
) -> None:
    """Critère 2, côté contrat : l'écran n'a plus à deviner qu'une question est posée."""
    fil = client_proposition.get(f"/api/chat/{NOM_ORCHESTRATION}").json()["messages"]

    assert [message["proposition"] for message in fil] == ["", OBJECTIF]


def test_le_geste_d_accord_ouvre_le_run_sans_repasser_par_le_juge(
    client_proposition, lanceur
) -> None:
    """Critère 1 : on répond d'un geste, et l'accord n'est pas un texte à rejuger."""
    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": True}
    )

    assert reponse.status_code == 201
    geste, repondu = reponse.json()["messages"]
    assert geste["auteur"] == UTILISATEUR and geste["contenu"] == "Oui, lance."
    assert repondu["run_id"] == "run-42"
    assert lanceur.objectifs == [OBJECTIF]
    # La demande est soldée : le fil a repris, plus rien n'attend.
    assert repondu["proposition"] == ""


def test_un_objectif_amende_est_celui_qui_part(client_proposition, lanceur) -> None:
    """Le troisième geste — amender —, et la raison pour laquelle il exige cette route.

    Le contrat du juge lui demande, sur un accord, de recopier **mot pour mot**
    sa propre proposition : une correction qui repasserait par lui serait
    silencieusement remplacée par l'original. Elle ne peut donc pas emprunter la
    zone de saisie, et le fil doit garder ce qui est réellement parti.
    """
    amende = "Développer une application Windows d'agenda, sans notifications"

    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
        json={"approuve": True, "objectif": amende},
    )

    geste, _ = reponse.json()["messages"]
    assert lanceur.objectifs == [amende]
    assert amende in geste["contenu"]


def test_un_objectif_intact_ne_se_recopie_pas(client_proposition, lanceur) -> None:
    """`objectif: null` n'est pas une omission, c'est « la proposition tient » (§6.10).

    Renvoyer le même texte serait indiscernable d'une correction, et le fil
    porterait « avec cet objectif : … » sur un objectif que personne n'a touché.
    """
    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
        json={"approuve": True, "objectif": None},
    )

    geste, _ = reponse.json()["messages"]
    assert geste["contenu"] == "Oui, lance."
    assert lanceur.objectifs == [OBJECTIF]


def test_un_refus_au_geste_n_ouvre_rien_et_laisse_parler(
    client_proposition, lanceur
) -> None:
    """Refuser n'annule aucun run — il n'y en a pas encore — et ne clôt pas le fil."""
    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": False}
    )

    assert reponse.status_code == 201
    geste, repondu = reponse.json()["messages"]
    assert geste["contenu"] == "Non, ne lance pas."
    assert lanceur.objectifs == []
    assert "je n'ouvre rien" in repondu["contenu"]


def test_le_geste_est_ecrit_au_fil_car_le_fil_est_la_seule_memoire(
    client_proposition, depot_chat
) -> None:
    """Un accord au bouton sans trace ferait reproposer le tour suivant.

    Le canal n'a pas d'autre mémoire que sa conversation (`orchestration`) : ce
    qui n'y est pas écrit n'a pas eu lieu pour le juge du message suivant.
    """
    client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": True}
    )

    fil = depot_chat.fil(NOM_ORCHESTRATION)
    assert [message.auteur for message in fil] == [
        UTILISATEUR,
        NOM_ORCHESTRATION,
        UTILISATEUR,
        NOM_ORCHESTRATION,
    ]
    assert fil[2].contenu == "Oui, lance."


def test_le_geste_part_aussi_sur_le_websocket(client_proposition) -> None:
    """Deux fenêtres ouvertes : celle qui n'a pas cliqué apprend que la demande est soldée."""
    with client_proposition.websocket_connect("/ws/evenements?projet=tous") as ws:
        client_proposition.post(
            f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": True}
        )
        aller = ws.receive_json()
        retour = ws.receive_json()

    assert aller["type"] == EVENEMENT_CHAT_MESSAGE and aller["run_id"] == ""
    assert retour["type"] == EVENEMENT_CHAT_MESSAGE and retour["run_id"] == "run-42"


def test_le_projet_de_la_fenetre_voyage_aussi_par_le_geste(
    client_proposition, lanceur
) -> None:
    """Un run ouvert au bouton appartient au projet actif, comme un run ouvert au clavier."""
    client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
        json={"approuve": True, "projet_id": "prj-depensio"},
    )

    assert lanceur.projets == ["prj-depensio"]


def test_un_geste_sans_demande_est_un_409(client_global, lanceur) -> None:
    """Rien n'attend : le fil de `client_global` n'a jamais rien proposé."""
    reponse = client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": True}
    )

    assert reponse.status_code == 409
    assert lanceur.objectifs == []


def test_un_second_geste_est_un_409_et_n_ouvre_pas_un_run_de_plus(
    client_proposition, lanceur
) -> None:
    """Le double geste — deux fenêtres, ou un clic répété — ne coûte pas deux runs.

    C'est le `409` de §6.10 un cran plus tôt, et il se tient **sans verrou** :
    le premier geste a écrit dans le fil, donc la demande n'est plus la dernière
    chose dite.
    """
    client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": True}
    )

    second = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": True}
    )

    assert second.status_code == 409
    assert lanceur.objectifs == [OBJECTIF]


def test_un_geste_sur_un_fil_inconnu_reste_un_404(client_proposition) -> None:
    reponse = client_proposition.post(
        "/api/chat/pas-un-agent/cadrage", json={"approuve": True}
    )

    assert reponse.status_code == 404


def test_une_ligne_ecrite_avant_ce_lot_se_relit_sans_demande() -> None:
    """Un fil persisté ne se réécrit pas : la clé absente retombe sur le défaut."""
    ancienne = {
        "agent": NOM_ORCHESTRATION,
        "auteur": NOM_ORCHESTRATION,
        "contenu": _propose(),
        "horodatage": "2026-09-11T12:38:00+00:00",
    }

    assert MessageChat.from_dict(ancienne).proposition == ""


# ── ⑦ borner le run depuis le chat (#990) ─────────────────────────────────────
#
# Le moteur savait arrêter un run sur quatre garde-fous ; la conversation, seule
# porte de lancement depuis #666, ne les passait pas — un run mesuré à 12,51 $
# n'a pas pu l'être. Ce qui se garde ici est la **traversée**, du corps HTTP
# jusqu'au lanceur, et ce que le fil en dit.
#
# Les bornes reçues sont lues sur le double (`LanceurEspion.bornes`) et jamais
# déduites d'un texte : un canal qui écrirait la bonne phrase en lançant sans
# borne serait exactement le défaut que ce ticket corrige, et il passerait un
# test de prose.


def test_les_quatre_bornes_traversent_le_geste_jusqu_au_lanceur(
    client_proposition, lanceur
) -> None:
    """Critère 1 : posables depuis l'interface, et **transmises** au lancement."""
    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
        json={
            "approuve": True,
            "plafond_cout_usd": 5,
            "plafond_tokens": 200000,
            "timeout_tache_s": 120,
            "parallelisme": 2,
        },
    )

    assert reponse.status_code == 201
    assert lanceur.bornes == [
        BornesRun(
            plafond_cout_usd=5.0,
            plafond_tokens=200000,
            timeout_tache_s=120.0,
            parallelisme=2,
        )
    ]


def test_un_geste_sans_borne_lance_sans_borne(client_proposition, lanceur) -> None:
    """Le corps d'avant #990 reste valable, et il vaut « aucune borne »."""
    client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": True}
    )

    assert lanceur.bornes == [AUCUNE_BORNE]


def test_le_fil_garde_la_trace_des_bornes_posees(client_proposition) -> None:
    """Le fil est la seule mémoire du canal : un run borné doit s'y relire.

    Sans cette trace, on retrouverait plus tard un run arrêté à 5 $ sans qu'aucune
    ligne ne dise que quelqu'un l'avait voulu.
    """
    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
        json={"approuve": True, "plafond_cout_usd": 5, "parallelisme": 2},
    )

    geste, _ = reponse.json()["messages"]
    assert geste["contenu"].startswith("Oui, lance — bornes : ")
    assert "5,00 $" in geste["contenu"]
    assert "2 tâches à la fois" in geste["contenu"]


def test_un_run_sans_borne_le_dit_a_son_lancement(client_proposition) -> None:
    """Critère 3 : l'illimité est un **choix affiché**, pas un oubli.

    Même règle que la ligne `plan :` d'un run d'outillage (#286) — le régime
    s'annonce dans les deux sens. C'est la réponse qui ouvre le run qui le dit,
    et non le geste : celui-ci ne porte que ce qu'il a **ajouté**.
    """
    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage", json={"approuve": True}
    )

    geste, repondu = reponse.json()["messages"]
    assert geste["contenu"] == "Oui, lance."
    assert "aucune borne : le run ira jusqu'au bout" in repondu["contenu"]


def test_un_run_borne_annonce_a_quoi_il_s_arretera(client_proposition) -> None:
    """L'autre sens du même régime : ce qui borne se lit à l'ouverture."""
    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
        json={"approuve": True, "plafond_cout_usd": 5},
    )

    _, repondu = reponse.json()["messages"]
    assert "s'interrompt à 5,00 $" in repondu["contenu"]
    assert "aucune borne" not in repondu["contenu"]


def test_un_refus_ne_borne_rien_parce_qu_il_n_ouvre_rien(
    client_proposition, lanceur
) -> None:
    """Les bornes suivent l'objectif : ignorées sur un refus, faute de run."""
    reponse = client_proposition.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
        json={"approuve": False, "plafond_cout_usd": 5},
    )

    geste, _ = reponse.json()["messages"]
    assert geste["contenu"] == "Non, ne lance pas."
    assert lanceur.bornes == []


def test_un_accord_tape_ne_porte_aucune_borne() -> None:
    """Le juge rend un objectif, jamais un formulaire — donc aucun run borné par lui.

    La distinction n'est pas cosmétique : c'est elle qui fait que les bornes ne
    peuvent venir que d'un **geste** d'écran, seul endroit où quelqu'un a pu les
    poser.
    """
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=lanceur
    )

    asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve()))

    assert lanceur.bornes == [AUCUNE_BORNE]


def test_une_borne_hors_bornes_est_refusee_par_le_moteur_et_racontee() -> None:
    """La règle « un plafond est un maximum » vit dans `lancer`, pas dans le canal.

    Le canal ne la redouble pas : il transmet, et il **raconte** le refus dans le
    fil plutôt que de le laisser remonter en 502 — la demande, elle, est acquise.
    """

    async def lanceur_du_vrai_service(
        objectif: str,
        projet_id: str | None = None,
        bornes: BornesRun = AUCUNE_BORNE,
        contexte_sources: str = "",
    ) -> dict[str, str]:
        plafond = bornes.plafond_cout_usd
        if plafond is not None and plafond <= 0:
            raise ValueError(f"plafond_cout_usd doit être > 0 (reçu : {plafond}).")
        return {"run_id": "run-42", "statut": "en_cours"}

    repondeur = RepondeurOrchestration(
        lanceur=lanceur_du_vrai_service,
        provider=JugeScripte(_verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF)),
    )

    reponse = asyncio.run(
        repondeur.trancher_cadrage(
            AGENT_ORCHESTRATION,
            [],
            approuve=True,
            objectif=OBJECTIF,
            bornes=BornesRun(plafond_cout_usd=0),
        )
    )

    assert reponse.run_id == ""
    assert "Le lancement a échoué" in reponse.contenu
    assert "doit être > 0" in reponse.contenu


@pytest.fixture()
def fournisseur_qui_propose(monkeypatch: pytest.MonkeyPatch) -> JugeScripte:
    """Le juge que `create_app` résoudra seul, et qui **propose** plutôt qu'il n'accorde.

    Jumeau de `fournisseur_par_defaut`, à un verdict près : le geste de cadrage
    exige une proposition **en attente** dans le fil, donc un premier tour qui en
    pose une.
    """
    juge = JugeScripte(_verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF))
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: juge
    )
    return juge


def test_les_bornes_du_geste_arrivent_au_moteur(
    bus, depot_chat, projets, moteur, fournisseur_qui_propose
) -> None:
    """La traversée **entière**, sans double de lanceur : corps HTTP → moteur.

    Les tests ci-dessus lisent un `LanceurEspion` : ils gardent le canal, pas le
    lanceur que `create_app` construit. Or c'est précisément lui qui était en
    défaut — `ouvrir_un_run` appelait `lancer(objectif)` sans garde-fou, et le
    run naissait sans borne quoi qu'on ait saisi. Celui-ci monte donc l'app
    **entière**, avec un moteur muet, et lit ce que le moteur a reçu.
    """
    with TestClient(
        create_app(
            bus=bus,
            state=ControlTowerState(),
            chat_store=depot_chat,
            projets=projets,
            fabrique_moteur=moteur,
        )
    ) as client:
        client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages",
            json={"contenu": "Génère une application d'agenda"},
        )
        reponse = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
            json={
                "approuve": True,
                "plafond_cout_usd": 5,
                "plafond_tokens": 200000,
                "timeout_tache_s": 120,
                "parallelisme": 2,
            },
        )
        assert reponse.status_code == 201

    assert moteur.garde_fous == [(5.0, 200000, 120.0, 2)]


# ── ⑪ un projet sans équipe se la voit proposer (#1146) ───────────────────────
#
# L'essai du 2026-09-21 : un run proposé puis ouvert sur un projet qui n'avait
# **aucun agent**, cadrage et plan payés (0,36 $), puis toutes les tâches en repli
# « à assigner ». Ce qui se garde ici est la conduite du canal, sans modèle ni
# moteur : la sonde d'équipe et le recruteur sont des doubles, comme le lanceur.
# Le câblage réel — sonde sur `catalogue_du_projet`, création par la voie de
# #1040, run qui aboutit — est joué de bout en bout dans
# `tests/test_projet_outille_http.py` (section ⑧).

#: Le projet de la fenêtre dans ces tests : un identifiant bien formé, dont seule
#: la sonde d'équipe décide s'il a quelqu'un.
PROJET_SANS_EQUIPE = "prj-9ab8b520"


def _roles_valides() -> list[RoleValide]:
    """L'équipe que l'écran rapporte : un rôle à deux instances, un rôle à une."""
    return [
        RoleValide(
            nom="dev-p1",
            role="Développeur",
            competences=("backend",),
            playbook="Tu écris le code de p1.",
            instances=2,
        ),
        RoleValide(nom="qa-p1", role="QA", competences=("tests",), playbook="Tu testes p1."),
    ]


def _corps_roles() -> list[dict[str, Any]]:
    """Les mêmes rôles dans la forme de `POST /api/projets/{id}/equipe`."""
    return [
        {
            "nom": r.nom,
            "role": r.role,
            "competences": list(r.competences),
            "playbook": r.playbook,
            "instances": r.instances,
        }
        for r in _roles_valides()
    ]


class RecruteurEspion:
    """Un `RecruteurEquipe` qui note ce qu'on lui demande et tient le compte des agents.

    Il **est** aussi la sonde d'équipe (`compte`) : une équipe créée par lui fait
    passer le projet de zéro à N agents, exactement comme la création réelle fait
    passer `catalogue_du_projet` du vide à l'équipe. C'est ce qui permet de jouer
    le protocole entier — équipe proposée, validée, puis run — sur un seul double.
    """

    def __init__(self, *, refus: Exception | None = None) -> None:
        self.appels: list[tuple[str, list[RoleValide], str]] = []
        self.agents: dict[str, int] = {}
        self._refus = refus

    def compte(self, projet_id: str) -> int | None:
        return self.agents.get(projet_id, 0)

    async def __call__(
        self, projet_id: str, roles: Any, proposition_id: str = ""
    ) -> dict[str, Any]:
        self.appels.append((projet_id, list(roles), proposition_id))
        if self._refus is not None:
            raise self._refus
        self.agents[projet_id] = sum(r.instances for r in roles)
        return {
            "projet_id": projet_id,
            "proposition_id": proposition_id,
            "cree": True,
            "agents": [{"nom": r.nom, "role": r.role, "instances": r.instances} for r in roles],
            "instances_total": sum(r.instances for r in roles),
        }


def _repondeur_sans_equipe(
    verdict: str,
    *,
    lanceur: LanceurEspion,
    recruteur: RecruteurEspion | None = None,
    equipe: Any = None,
) -> RepondeurOrchestration:
    """Le répondeur d'un projet sans agent : la sonde dit zéro tant que rien n'est créé."""
    recruteur = recruteur if recruteur is not None else RecruteurEspion()
    return RepondeurOrchestration(
        lanceur=lanceur,
        provider=JugeScripte(verdict),
        equipe=equipe if equipe is not None else recruteur.compte,
        recruteur=recruteur,
    )


def test_un_projet_sans_equipe_se_voit_proposer_son_equipe_et_non_un_run() -> None:
    """Critère 1 : pas de run voué à l'échec — l'équipe, et pourquoi.

    L'échantillon fautif est le fil de l'essai : la proposition d'un run
    (`proposition` posée, « Je lance ? ») sur un projet où `GET /api/agents`
    rendait `[]`. Le test lit la **structure** de la réponse — plus de demande de
    cadrage, une demande de recrutement qui porte l'objectif et le projet — puis
    ce que la phrase dit : aucun agent, donc personne pour les tâches.
    """
    lanceur = LanceurEspion()
    repondeur = _repondeur_sans_equipe(
        _verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF), lanceur=lanceur
    )

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION,
            _fil("Peux-tu vider le dossier du projet ?"),
            projet_id=PROJET_SANS_EQUIPE,
        )
    )

    assert reponse.proposition == ""
    assert reponse.recrutement == DemandeRecrutement(
        objectif=OBJECTIF, projet_id=PROJET_SANS_EQUIPE
    )
    assert "aucun agent" in reponse.contenu
    assert "personne pour prendre les tâches" in reponse.contenu
    # Le « Je lance ? » du juge n'est pas écrit : il contredirait la phrase.
    assert _propose() not in reponse.contenu
    assert lanceur.objectifs == []


def test_un_projet_equipe_garde_la_proposition_d_un_run() -> None:
    """Le témoin : avec quelqu'un pour prendre les tâches, rien ne change."""
    lanceur = LanceurEspion()
    repondeur = _repondeur_sans_equipe(
        _verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF),
        lanceur=lanceur,
        equipe=lambda projet_id: 3,
    )

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil("Ajoute la pagination"), projet_id=PROJET_SANS_EQUIPE
        )
    )

    assert reponse.proposition == OBJECTIF
    assert reponse.recrutement is None


def _sonde_qui_tombe(projet_id: str) -> int | None:
    raise OSError("dépôt d'agents illisible")


@pytest.mark.parametrize(
    ("equipe", "projet_id"),
    [
        pytest.param(lambda projet_id: None, PROJET_SANS_EQUIPE, id="sonde-qui-ne-sait-pas"),
        pytest.param(_sonde_qui_tombe, PROJET_SANS_EQUIPE, id="sonde-qui-tombe"),
        pytest.param(lambda projet_id: 0, None, id="sans-projet"),
    ],
)
def test_une_sonde_qui_ne_sait_pas_ne_bride_aucun_run(equipe: Any, projet_id: Any) -> None:
    """« Je ne sais pas » n'est pas « il n'y a personne » (#1042) : le canal reste tel quel.

    Une sonde aveugle qui bloquerait les runs serait une bride (docs/41) : le
    canal ne détourne une demande que sur un compte **nul et certain**.
    """
    lanceur = LanceurEspion()
    repondeur = _repondeur_sans_equipe(
        _verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF),
        lanceur=lanceur,
        equipe=equipe,
    )

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil("Ajoute la pagination"), projet_id=projet_id)
    )

    assert reponse.proposition == OBJECTIF
    assert reponse.recrutement is None


def test_un_accord_tape_sur_un_projet_sans_equipe_n_ouvre_aucun_run() -> None:
    """La garde au verdict vaut aussi pour « oui » : c'est lui qui ouvrait le run de l'essai."""
    lanceur = LanceurEspion()
    repondeur = _repondeur_sans_equipe(
        _verdict(VERDICT_ACCORD, "C'est parti.", OBJECTIF), lanceur=lanceur
    )

    reponse = asyncio.run(
        repondeur.produire(AGENT_ORCHESTRATION, _fil_approuve(), projet_id=PROJET_SANS_EQUIPE)
    )

    assert lanceur.objectifs == []
    assert reponse.run_id == ""
    assert reponse.recrutement is not None
    assert "C'est parti" not in reponse.contenu


def test_le_geste_de_cadrage_sur_un_projet_sans_equipe_propose_l_equipe() -> None:
    """La seconde garde : une équipe retirée entre la proposition et le clic.

    Sans elle, un accord au bouton ouvrirait le run que le verdict n'aurait plus
    proposé — et « c'est parti » serait écrit juste avant l'échec.
    """
    lanceur = LanceurEspion()
    repondeur = _repondeur_sans_equipe(_verdict(VERDICT_ECHANGE, "sans objet"), lanceur=lanceur)

    reponse = asyncio.run(
        repondeur.trancher_cadrage(
            AGENT_ORCHESTRATION,
            [],
            approuve=True,
            objectif=OBJECTIF,
            projet_id=PROJET_SANS_EQUIPE,
        )
    )

    assert lanceur.objectifs == []
    assert reponse.recrutement == DemandeRecrutement(
        objectif=OBJECTIF, projet_id=PROJET_SANS_EQUIPE
    )
    assert "C'est parti" not in reponse.contenu


def test_sans_recruteur_le_manque_est_dit_sans_demande() -> None:
    """Pas de demande à laquelle aucun geste ne pourrait répondre : le canal dit où créer."""
    lanceur = LanceurEspion()
    repondeur = RepondeurOrchestration(
        lanceur=lanceur,
        provider=JugeScripte(_verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF)),
        equipe=lambda projet_id: 0,
    )

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil("Ajoute la pagination"), projet_id=PROJET_SANS_EQUIPE
        )
    )

    assert reponse.recrutement is None
    assert reponse.proposition == ""
    assert "écrans d'agents" in reponse.contenu
    assert lanceur.objectifs == []


def _demande() -> DemandeRecrutement:
    return DemandeRecrutement(objectif=OBJECTIF, projet_id=PROJET_SANS_EQUIPE)


def test_l_equipe_validee_est_creee_puis_la_demande_d_origine_est_reproposee() -> None:
    """Critère 1, seconde moitié : créée par la voie existante, puis le run est **proposé**.

    Proposé, pas ouvert : valider une équipe n'est pas accorder un run (#685).
    La réponse porte donc une demande de cadrage sur l'objectif d'origine — celui
    de la demande de recrutement, pas un texte à retaper.
    """
    lanceur = LanceurEspion()
    recruteur = RecruteurEspion()
    repondeur = _repondeur_sans_equipe(
        _verdict(VERDICT_ECHANGE, "sans objet"), lanceur=lanceur, recruteur=recruteur
    )

    reponse = asyncio.run(
        repondeur.recruter(
            AGENT_ORCHESTRATION,
            [],
            demande=_demande(),
            approuve=True,
            roles=_roles_valides(),
            proposition_id="equ-42",
        )
    )

    assert recruteur.appels == [(PROJET_SANS_EQUIPE, _roles_valides(), "equ-42")]
    assert "Équipe créée : Développeur ×2 · QA — 3 agents" in reponse.contenu
    assert reponse.proposition == OBJECTIF
    assert reponse.recrutement is None
    assert lanceur.objectifs == []


def test_decliner_l_equipe_ne_cree_rien_et_n_ouvre_rien() -> None:
    lanceur = LanceurEspion()
    recruteur = RecruteurEspion()
    repondeur = _repondeur_sans_equipe(
        _verdict(VERDICT_ECHANGE, "sans objet"), lanceur=lanceur, recruteur=recruteur
    )

    reponse = asyncio.run(
        repondeur.recruter(AGENT_ORCHESTRATION, [], demande=_demande(), approuve=False, roles=())
    )

    assert recruteur.appels == []
    assert lanceur.objectifs == []
    assert reponse.proposition == "" and reponse.recrutement is None
    assert "je ne recrute personne" in reponse.contenu


def test_une_equipe_refusee_est_racontee_et_la_demande_reste_posee() -> None:
    """Rien n'est créé (la création vérifie tout d'abord) : la cause, et on recommence.

    La demande est **reposée** telle quelle, sans quoi corriger l'équipe
    obligerait à redire sa demande de travail pour qu'on la repropose.
    """
    recruteur = RecruteurEspion(refus=ValueError("dev-p1 : nom déjà pris dans ce projet"))
    repondeur = _repondeur_sans_equipe(
        _verdict(VERDICT_ECHANGE, "sans objet"), lanceur=LanceurEspion(), recruteur=recruteur
    )

    reponse = asyncio.run(
        repondeur.recruter(
            AGENT_ORCHESTRATION,
            [],
            demande=_demande(),
            approuve=True,
            roles=_roles_valides(),
        )
    )

    assert "Je n'ai créé aucun agent" in reponse.contenu
    assert "nom déjà pris" in reponse.contenu
    assert reponse.recrutement == _demande()
    assert reponse.proposition == ""


def test_un_repondeur_qui_ne_propose_pas_d_equipe_n_a_rien_a_valider() -> None:
    with pytest.raises(RecrutementIntrouvable):
        asyncio.run(
            RepondeurScripte().recruter(
                AGENT_ORCHESTRATION, [], demande=_demande(), approuve=True, roles=()
            )
        )


def test_une_equipe_proposee_attend_tant_que_rien_n_a_suivi() -> None:
    """La règle des deux autres demandes, et un fil d'avant ce lot qui se relit sans elle."""
    demande = MessageChat(
        agent=NOM_ORCHESTRATION,
        auteur=NOM_ORCHESTRATION,
        contenu="Il faut une équipe.",
        recrutement=_demande(),
    )
    suite = MessageChat(agent=NOM_ORCHESTRATION, auteur=UTILISATEUR, contenu="plus tard")

    assert recrutement_en_attente([demande]) is demande
    assert recrutement_en_attente([demande, suite]) is None
    assert recrutement_en_attente(_fil("bonjour")) is None
    relue = MessageChat.from_dict(demande.to_ligne())
    assert relue.recrutement == _demande()
    ancienne = {"agent": NOM_ORCHESTRATION, "auteur": NOM_ORCHESTRATION, "contenu": "x"}
    assert MessageChat.from_dict(ancienne).recrutement is None


@pytest.fixture()
def recruteur() -> RecruteurEspion:
    return RecruteurEspion()


@pytest.fixture()
def client_sans_equipe(bus, depot_chat, lanceur, recruteur):
    """L'app dont le projet de la fenêtre n'a personne : la demande a été faite."""
    with TestClient(
        create_app(
            bus=bus,
            chat_store=depot_chat,
            orchestration_repondeur=_repondeur_sans_equipe(
                _verdict(VERDICT_PROPOSITION, _propose(), OBJECTIF),
                lanceur=lanceur,
                recruteur=recruteur,
            ),
        )
    ) as client:
        reponse = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages",
            json={
                "contenu": "Peux-tu vider le dossier du projet ?",
                "projet_id": PROJET_SANS_EQUIPE,
            },
        )
        assert reponse.status_code == 201, reponse.text
        yield client


def test_le_protocole_entier_equipe_proposee_validee_puis_run(
    client_sans_equipe, lanceur, recruteur, depot_chat
) -> None:
    """L'oracle de S3 au niveau du canal (docs/40 §5) : l'équipe, puis le run aboutit.

    Trois tours, sans quitter la conversation : la demande reçoit une équipe au
    lieu d'un run ; l'équipe validée d'un geste est créée et la demande reprise ;
    le run proposé part sur l'objectif d'origine. Le projet où l'équipe naît est
    celui de la **demande** — le corps du geste n'en porte aucun.
    """
    fil = client_sans_equipe.get(f"/api/chat/{NOM_ORCHESTRATION}").json()["messages"]
    assert fil[-1]["recrutement"] == {"objectif": OBJECTIF, "projet_id": PROJET_SANS_EQUIPE}
    assert fil[-1]["proposition"] == ""

    reponse = client_sans_equipe.post(
        f"/api/chat/{NOM_ORCHESTRATION}/recrutement",
        json={"approuve": True, "roles": _corps_roles(), "proposition_id": "equ-42"},
    )

    assert reponse.status_code == 201, reponse.text
    geste, repondu = reponse.json()["messages"]
    assert geste["auteur"] == UTILISATEUR
    assert geste["contenu"] == "Je valide cette équipe : Développeur ×2 · QA."
    assert [appel[0] for appel in recruteur.appels] == [PROJET_SANS_EQUIPE]
    assert repondu["proposition"] == OBJECTIF and repondu["recrutement"] is None

    lance = client_sans_equipe.post(
        f"/api/chat/{NOM_ORCHESTRATION}/cadrage",
        json={"approuve": True, "projet_id": PROJET_SANS_EQUIPE},
    )

    assert lance.status_code == 201, lance.text
    assert lance.json()["messages"][1]["run_id"] == "run-42"
    assert lanceur.objectifs == [OBJECTIF]
    assert lanceur.projets == [PROJET_SANS_EQUIPE]
    # Le fil garde les six tours : c'est sa seule mémoire.
    assert [m.auteur for m in depot_chat.fil(NOM_ORCHESTRATION)] == [
        UTILISATEUR,
        NOM_ORCHESTRATION,
    ] * 3


def test_un_second_geste_de_recrutement_est_un_409_et_ne_cree_rien_de_plus(
    client_sans_equipe, recruteur
) -> None:
    corps = {"approuve": True, "roles": _corps_roles()}
    client_sans_equipe.post(f"/api/chat/{NOM_ORCHESTRATION}/recrutement", json=corps)

    second = client_sans_equipe.post(f"/api/chat/{NOM_ORCHESTRATION}/recrutement", json=corps)

    assert second.status_code == 409
    assert len(recruteur.appels) == 1


def test_un_geste_de_recrutement_sans_demande_est_un_409(client_global) -> None:
    reponse = client_global.post(
        f"/api/chat/{NOM_ORCHESTRATION}/recrutement",
        json={"approuve": True, "roles": _corps_roles()},
    )

    assert reponse.status_code == 409


def test_valider_une_equipe_vide_est_un_422_et_rien_n_est_ecrit(
    client_sans_equipe, recruteur, depot_chat
) -> None:
    """« Ne pas recruter » se dit en déclinant : même règle que `POST …/equipe`."""
    avant = len(depot_chat.fil(NOM_ORCHESTRATION))

    reponse = client_sans_equipe.post(
        f"/api/chat/{NOM_ORCHESTRATION}/recrutement", json={"approuve": True, "roles": []}
    )

    assert reponse.status_code == 422
    assert recruteur.appels == []
    assert len(depot_chat.fil(NOM_ORCHESTRATION)) == avant


def test_decliner_au_geste_laisse_le_fil_sans_demande(client_sans_equipe, recruteur) -> None:
    reponse = client_sans_equipe.post(
        f"/api/chat/{NOM_ORCHESTRATION}/recrutement", json={"approuve": False}
    )

    assert reponse.status_code == 201, reponse.text
    geste, repondu = reponse.json()["messages"]
    assert geste["contenu"] == "Pas d'équipe pour l'instant."
    assert recruteur.appels == []
    assert repondu["recrutement"] is None and repondu["proposition"] == ""
