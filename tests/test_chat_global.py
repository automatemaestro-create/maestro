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
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.chat import (
    CONVERSATION_ORIGINE,
    FRAGMENT_CHAT_DEBUT,
    FRAGMENT_CHAT_DELTA,
    FRAGMENT_CHAT_FIN,
    ChatStore,
    MessageChat,
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
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    VERDICT_ACCORD,
    VERDICT_ECHANGE,
    VERDICT_PROPOSITION,
    RepondeurOrchestration,
    apercu_de,
)
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.state import (
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    VALIDATION_EN_ATTENTE,
    ControlTowerState,
)
from maestro.engine import RunReport
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

    Il note **les deux** arguments du contrat (#683) : l'objectif et le projet
    de la fenêtre. Un double qui n'accepterait que le premier rendrait le canal
    vert sur un lancement que le vrai service refuserait — et le répondeur
    rattrapant toute exception du lanceur, l'échec se lirait « le lancement a
    échoué » au lieu d'une erreur de signature.
    """

    def __init__(self, *, run_id: str = "run-42", statut: str = "en_cours") -> None:
        self.objectifs: list[str] = []
        self.projets: list[str | None] = []
        self._resume = {"run_id": run_id, "statut": statut}

    async def __call__(self, objectif: str, projet_id: str | None = None) -> dict[str, str]:
        self.objectifs.append(objectif)
        self.projets.append(projet_id)
        return dict(self._resume)


def _repondeur(
    reponse_du_juge: str, *, lanceur: LanceurEspion | None = None, apercu: Any = None
) -> tuple[RepondeurOrchestration, JugeScripte]:
    """Le répondeur et son juge, montés ensemble — les tests ont besoin des deux."""
    juge = JugeScripte(reponse_du_juge)
    return (
        RepondeurOrchestration(lanceur=lanceur, apercu=apercu, provider=juge),
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


# ── ③ le répondeur : ce qu'il ouvre, et sur quel objectif ─────────────────────


def _fil_approuve() -> list[MessageChat]:
    """Le fil d'un accord : la demande, la proposition du canal, le « oui »."""
    return _fil(
        "J'aimerai que tu me génère le projet p1 comme une application windows d'agenda",
        _propose(),
        "oui",
    )


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

    async def lanceur_qui_echoue(objectif: str, projet_id: str | None = None) -> dict[str, str]:
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
    d'autre. Le `strip` final est celui de `_Redaction.texte`, d'où la
    comparaison sur le texte ébarbé plutôt que sur la somme brute.
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

    def __call__(self, **reglages: Any) -> MoteurMuet:
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
def client_reel(bus, depot_chat, projets, moteur, fournisseur_par_defaut):
    """L'app **entière** : vrai répondeur d'orchestration, vrai service d'exécutions."""
    with TestClient(
        create_app(
            bus=bus,
            state=ControlTowerState(),
            chat_store=depot_chat,
            projets=projets,
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
    # Aucun état de session : le répondeur porte **exactement** les trois
    # collaborateurs qu'on lui a passés, et pas un attribut de plus où loger une
    # proposition en attente. C'est cette forme-là qu'on garde plutôt qu'une
    # recherche de l'objectif dans `vars()` — un objectif rangé dans un objet
    # imbriqué y échapperait, alors qu'un attribut nouveau, lui, se voit toujours.
    assert set(vars(repondeur)) == {"_lanceur", "_apercu", "_provider"}


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

#: `AMORCES_ORCHESTRATION` / `AMORCES_ASSISTANCE` (côté TypeScript) ne sont **pas**
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
