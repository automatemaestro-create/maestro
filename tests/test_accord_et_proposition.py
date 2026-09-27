"""Un accord tapé porte ses bornes, une proposition vient avec son estimation et survit
aux questions qui la suivent (#1184).

Trois raideurs du fil au moment de lancer, relevées le 2026-09-21 par les balayages
« rien de figé », et chacune a ici son échantillon :

- **un accord tapé perdait ses bornes** — « vas-y, 5 $ max » ouvrait un run sans
  aucune borne, le verdict du juge n'ayant aucun champ pour elles
  (`RepondeurOrchestration.produire`, `_Verdict.bornes`) ;
- **une proposition mourait à la première question** — « combien ça coûtera ? »
  faisait disparaître la carte d'accord, qui ne tenait qu'au dernier message
  (`proposition_en_attente`, `_Verdict.garde`) ;
- **aucune estimation ne l'accompagnait** — l'accord se donnait sur des nombres bruts
  (`maestro.controltower.estimation`, `MessageChat.estimation`).

Le juge est un double : la **qualité** du jugement (reconnaître « 5 $ max », juger
qu'une question porte sur la proposition) n'est pas tenue ici. Ce qui l'est, c'est ce
que le juge **reçoit** et ce que le canal **fait** de son verdict — ce qui part au
lanceur, ce qui voyage sur le message, ce que l'écran relira.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.controltower.chat import (
    ChatStore,
    MessageChat,
    ProjetVise,
    proposition_en_attente,
)
from maestro.controltower.estimation import (
    COUT_DECOMPOSITION_USD,
    COUT_TACHE_USD_BAS,
    COUT_TACHE_USD_HAUT,
    MARGE_RELANCES,
    NB_TACHES_PLANCHER,
    EstimationRun,
    estimer_run,
)
from maestro.controltower.events import InMemoryEventBus
from maestro.controltower.orchestration import (
    _PROMPT_ORCHESTRATION,
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    VERDICT_ACCORD,
    VERDICT_ECHANGE,
    VERDICT_PROPOSITION,
    RepondeurOrchestration,
)
from maestro.controltower.regime import REGLE_DES_BORNES
from maestro.providers.base import ModelProvider

UTILISATEUR = "utilisateur"

#: L'objectif que le juge propose puis recopie — il ne ressemble à aucun message du
#: fil, pour qu'on voie à l'assertion que c'est lui qui part.
OBJECTIF = "Ajouter la pagination à la liste des dépenses"

#: Le projet où la proposition est faite, et celui d'une autre fenêtre : le fil est
#: transverse (#281), une question peut venir d'ailleurs.
ICI = "prj-depenses"
AILLEURS = "prj-agenda"

#: Le marqueur du contrat (#1222), tel que le modèle l'écrit.
MARQUEUR = "%%MAESTRO%%"


def _dicte(reponse: str, nom: str, objectif: str = "", **cles: Any) -> str:
    """La réponse du modèle au contrat : la prose, puis la dernière ligne marquée."""
    charge = json.dumps({"verdict": nom, "objectif": objectif, **cles}, ensure_ascii=False)
    return f"{reponse}\n{MARQUEUR} {charge}"


class JugeEnSequence(ModelProvider):
    """Rend un verdict différent à chaque jugement, et range ce qu'il a reçu.

    Les rédactions d'après un geste (#1262) se reconnaissent à leur prompt système —
    ni celui du juge, ni celui du tour de lecture — et reçoivent une phrase fixe :
    elles ne consomment pas la séquence, qui ne parle que des jugements.
    """

    name = "juge-en-sequence"

    def __init__(self, *verdicts: str) -> None:
        self._verdicts = list(verdicts)
        self.jugements: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        if system_prompt != _PROMPT_ORCHESTRATION:
            return "C'est noté."
        self.jugements.append(prompt)
        if not self._verdicts:
            raise AssertionError("le juge a été appelé plus de fois que prévu")
        return self._verdicts.pop(0)


class LanceurEspion:
    """Un `LanceurRun` qui note l'objectif, le projet et les **bornes** reçus."""

    def __init__(self) -> None:
        self.objectifs: list[str] = []
        self.projets: list[str | None] = []
        self.bornes: list[BornesRun] = []

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
        return {"run_id": "run-42", "statut": "en_cours"}


def _repondeur(*verdicts: str, lanceur: LanceurEspion | None = None):
    juge = JugeEnSequence(*verdicts)
    return RepondeurOrchestration(lanceur=lanceur or LanceurEspion(), provider=juge), juge


def _message(auteur: str, contenu: str, **champs: Any) -> MessageChat:
    return MessageChat(agent=NOM_ORCHESTRATION, auteur=auteur, contenu=contenu, **champs)


def _proposition(**champs: Any) -> MessageChat:
    """La réponse qui propose : l'objectif, son projet, son estimation — ce que la carte montre."""
    champs.setdefault("projet_vise", ProjetVise(id=ICI, nom="Dépenses"))
    champs.setdefault("estimation", estimer_run(5))
    return _message(
        NOM_ORCHESTRATION,
        f"J'ouvrirais un run sur : « {OBJECTIF} ». Je lance ?",
        proposition=OBJECTIF,
        **champs,
    )


def _fil(*suite: MessageChat) -> list[MessageChat]:
    return [_message(UTILISATEUR, "Ajoute la pagination aux dépenses"), _proposition(), *suite]


def _produire(repondeur: RepondeurOrchestration, fil, projet_id: str | None = ICI):
    return asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, fil, projet_id=projet_id))


# ── ① un accord tapé porte les bornes qu'il nomme ───────────────────────────────


def test_un_accord_tape_qui_nomme_un_plafond_de_cout_lance_le_run_avec_lui() -> None:
    """« Vas-y, 5 $ max » : le plafond part au lanceur, et plus aucune borne n'est perdue."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _dicte(
            "C'est parti, avec un plafond de 5 $.",
            VERDICT_ACCORD,
            OBJECTIF,
            bornes={"plafond_cout_usd": 5},
        ),
        lanceur=lanceur,
    )

    reponse = _produire(repondeur, _fil(_message(UTILISATEUR, "vas-y, 5 $ max")))

    assert lanceur.bornes == [BornesRun(plafond_cout_usd=5.0)]
    assert reponse.run_id == "run-42"


def test_une_duree_nommee_a_l_accord_borne_chaque_tache() -> None:
    """La durée que Maestro sait borner est celle d'une tâche : elle part telle quelle."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _dicte(
            "C'est parti : pas plus de dix minutes par tâche.",
            VERDICT_ACCORD,
            OBJECTIF,
            bornes={"timeout_tache_s": 600, "plafond_cout_usd": 3.5},
        ),
        lanceur=lanceur,
    )

    _produire(repondeur, _fil(_message(UTILISATEUR, "ok, 3,50 $ et 10 min par tâche maxi")))

    assert lanceur.bornes == [BornesRun(plafond_cout_usd=3.5, timeout_tache_s=600.0)]


def test_une_borne_que_le_modele_ecrit_en_chaine_n_est_pas_perdue() -> None:
    """« "5" » au lieu de 5 : perdre la borne ferait exactement le défaut du ticket."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _dicte("C'est parti.", VERDICT_ACCORD, OBJECTIF, bornes={"plafond_cout_usd": "5"}),
        lanceur=lanceur,
    )

    _produire(repondeur, _fil(_message(UTILISATEUR, "vas-y, 5 $ max")))

    assert lanceur.bornes == [BornesRun(plafond_cout_usd=5.0)]


def test_un_accord_tape_sans_borne_nommee_n_en_invente_aucune() -> None:
    """Aucune borne par défaut (#494) : un « oui » nu lance sans borne, comme avant."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(_dicte("C'est parti.", VERDICT_ACCORD, OBJECTIF), lanceur=lanceur)

    _produire(repondeur, _fil(_message(UTILISATEUR, "oui")))

    assert lanceur.bornes == [AUCUNE_BORNE]


def test_les_bornes_appliquees_voyagent_sur_la_reponse_qui_ouvre_le_run() -> None:
    """Le fil répète les bornes **appliquées** : celles que le lanceur a reçues, pas des mots.

    Elles sont un fait de la réponse, comme `run_id` — c'est ce que l'écran relit
    sous la bulle, dans les mots de Maestro (`phraseDesBornes`), et non ce que le
    modèle a cru poser.
    """
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(
        _dicte("C'est parti.", VERDICT_ACCORD, OBJECTIF, bornes={"plafond_cout_usd": 5}),
        lanceur=lanceur,
    )

    reponse = _produire(repondeur, _fil(_message(UTILISATEUR, "vas-y, 5 $ max")))

    assert reponse.bornes == lanceur.bornes[0]
    # Et « aucune » se dit aussi : l'illimité est un choix affiché, pas un oubli (#990).
    repondeur, _ = _repondeur(_dicte("C'est parti.", VERDICT_ACCORD, OBJECTIF))
    assert _produire(repondeur, _fil(_message(UTILISATEUR, "oui"))).bornes == AUCUNE_BORNE


def test_un_lancement_refuse_ne_pretend_aucune_borne() -> None:
    """Rien ne s'est ouvert : aucune borne n'a été appliquée, et le fait n'en dit aucune."""

    async def lanceur_qui_refuse(
        objectif: str, projet_id: str | None = None, bornes=AUCUNE_BORNE, contexte="",
    ) -> dict[str, str]:
        raise ValueError("plafond_cout_usd doit être > 0 (reçu : -5).")

    repondeur = RepondeurOrchestration(
        lanceur=lanceur_qui_refuse,
        provider=JugeEnSequence(
            _dicte("C'est parti.", VERDICT_ACCORD, OBJECTIF, bornes={"plafond_cout_usd": -5})
        ),
    )

    reponse = _produire(repondeur, _fil(_message(UTILISATEUR, "vas-y, -5 $")))

    assert reponse.run_id == "" and reponse.bornes is None
    assert "doit être > 0" in reponse.contenu


def test_le_contrat_du_juge_porte_les_bornes_de_l_accord() -> None:
    """Le juge ne peut rendre que ce que son contrat lui décrit : les bornes y sont, sur l'accord.

    Et le fait que tous les appels reçoivent (« ce qu'un run fera ») ne dit plus
    qu'un accord tapé n'en pose aucune — c'était la phrase qui rendait le défaut
    exact.
    """
    _, trouve, apres = _PROMPT_ORCHESTRATION.partition('Sur "accord" qui approuve un run')
    assert trouve, "le contrat ne décrit pas les bornes d'un accord"
    section = apres.split("\nSur ", 1)[0]
    for cle in ("plafond_cout_usd", "plafond_tokens", "timeout_tache_s", "parallelisme"):
        assert cle in section
    assert "n'en pose aucune" not in REGLE_DES_BORNES


# ── ② une proposition survit aux questions qui la suivent ────────────────────────


def test_une_question_sur_la_proposition_la_garde_acceptable() -> None:
    """« Combien ça coûtera ? » répond **et** garde la carte : le juge dit qu'elle tient."""
    repondeur, _ = _repondeur(
        _dicte(
            "Autour de 5 à 10 $ en ordre de grandeur.",
            VERDICT_ECHANGE,
            garde_la_proposition=True,
        )
    )

    reponse = _produire(repondeur, _fil(_message(UTILISATEUR, "combien ça coûtera ?")))

    assert reponse.proposition == OBJECTIF
    # Recopiée du fil, jamais réécrite : son projet et son estimation sont ceux que
    # la carte montrait.
    assert reponse.projet_vise == ProjetVise(id=ICI, nom="Dépenses")
    assert reponse.estimation == estimer_run(5)
    assert reponse.run_id == ""


def test_une_question_posee_d_une_autre_fenetre_garde_le_projet_de_la_proposition() -> None:
    """Le fil est transverse : la carte gardée travaillera toujours là où elle a été proposée."""
    repondeur, _ = _repondeur(
        _dicte("Environ quatre tâches.", VERDICT_ECHANGE, garde_la_proposition=True)
    )

    reponse = _produire(
        repondeur, _fil(_message(UTILISATEUR, "ça fera combien de tâches ?")), projet_id=AILLEURS
    )

    assert reponse.projet_vise is not None and reponse.projet_vise.id == ICI


@pytest.mark.parametrize(
    "verdict",
    [
        pytest.param(_dicte("Entendu, je n'ouvre rien.", VERDICT_ECHANGE), id="refus-tape"),
        pytest.param(
            _dicte("Deux runs tournent.", VERDICT_ECHANGE, garde_la_proposition=False),
            id="autre-sujet",
        ),
    ],
)
def test_ce_qui_ne_garde_pas_la_proposition_la_retire(verdict: str) -> None:
    """Un refus tapé, ou un autre sujet : la carte tombe, comme avant ce ticket."""
    repondeur, _ = _repondeur(verdict)

    reponse = _produire(repondeur, _fil(_message(UTILISATEUR, "plutôt pas, finalement")))

    assert reponse.proposition == "" and reponse.estimation is None


def test_garder_sans_proposition_en_attente_ne_fabrique_rien() -> None:
    """Le juge qui « garde » ce qui n'existe pas ne fait naître aucune carte."""
    repondeur, _ = _repondeur(
        _dicte("Bonjour !", VERDICT_ECHANGE, garde_la_proposition=True)
    )

    reponse = _produire(repondeur, [_message(UTILISATEUR, "bonjour")])

    assert reponse.proposition == ""


def test_le_juge_voit_la_proposition_qui_attend_avec_son_estimation() -> None:
    """Pour répondre « combien ? » et recopier l'objectif, le juge lit la carte en attente."""
    repondeur, juge = _repondeur(
        _dicte("Autour de 5 à 10 $.", VERDICT_ECHANGE, garde_la_proposition=True)
    )

    _produire(repondeur, _fil(_message(UTILISATEUR, "combien ça coûtera ?")))

    prompt = juge.jugements[0]
    assert "proposition de run attend" in prompt
    assert f"« {OBJECTIF} »" in prompt
    assert "4,50 $ à 9,90 $" in prompt


def test_plusieurs_questions_puis_le_geste_d_accord_ouvrent_le_run_propose(tmp_path) -> None:
    """Le protocole entier, sur la route : proposer, deux questions, puis accepter d'un clic.

    Avant ce ticket le geste rendait `409` dès la première question — la demande
    n'était plus « le dernier message ». Le run part sur l'objectif **proposé**, dans
    le projet **de la proposition**, avec les bornes du geste et rien de plus.
    """
    lanceur = LanceurEspion()
    juge = JugeEnSequence(
        _dicte("Je peux lancer ça. Je lance ?", VERDICT_PROPOSITION, OBJECTIF, taches=5),
        _dicte("Autour de 4,50 à 9,90 $.", VERDICT_ECHANGE, garde_la_proposition=True),
        _dicte("Cinq tâches environ.", VERDICT_ECHANGE, garde_la_proposition=True),
    )
    repondeur = RepondeurOrchestration(lanceur=lanceur, provider=juge)
    chemin = f"/api/chat/{NOM_ORCHESTRATION}"
    with TestClient(
        create_app(
            bus=InMemoryEventBus(),
            chat_store=ChatStore(tmp_path / "chat"),
            orchestration_repondeur=repondeur,
        )
    ) as client:
        for contenu, projet in (
            ("Ajoute la pagination aux dépenses", ICI),
            ("combien ça coûtera ?", ICI),
            ("et combien de tâches ?", AILLEURS),
        ):
            reponse = client.post(
                f"{chemin}/messages", json={"contenu": contenu, "projet_id": projet}
            ).json()["messages"][-1]
            assert reponse["proposition"] == OBJECTIF
            assert reponse["estimation"]["taches"] == 5
        accord = client.post(
            f"{chemin}/cadrage",
            json={"approuve": True, "projet_id": AILLEURS, "plafond_cout_usd": 8},
        )

    assert accord.status_code == 201
    assert lanceur.objectifs == [OBJECTIF]
    assert lanceur.projets == [ICI]
    assert lanceur.bornes == [BornesRun(plafond_cout_usd=8.0)]
    # Les bornes appliquées sont sur la réponse, et donc sous la bulle.
    assert accord.json()["messages"][-1]["bornes"]["plafond_cout_usd"] == 8.0


def test_un_accord_tape_apres_des_questions_ouvre_le_run_de_la_proposition() -> None:
    """La même chose au clavier : « bon, vas-y, 8 $ max » après deux questions."""
    lanceur = LanceurEspion()
    gardee = _message(
        NOM_ORCHESTRATION,
        "Cinq tâches environ.",
        proposition=OBJECTIF,
        projet_vise=ProjetVise(id=ICI),
        estimation=estimer_run(5),
    )
    repondeur, _ = _repondeur(
        _dicte(
            "C'est parti, plafond 8 $.", VERDICT_ACCORD, OBJECTIF, bornes={"plafond_cout_usd": 8}
        ),
        lanceur=lanceur,
    )

    _produire(
        repondeur,
        _fil(
            _message(UTILISATEUR, "et combien de tâches ?"),
            gardee,
            _message(UTILISATEUR, "bon, vas-y, 8 $ max"),
        ),
        projet_id=AILLEURS,
    )

    assert lanceur.projets == [ICI]
    assert lanceur.bornes == [BornesRun(plafond_cout_usd=8.0)]


def test_la_regle_d_attente_reste_le_dernier_message() -> None:
    """Rien n'est deviné derrière le fil : c'est la réponse qui garde, pas une mémoire.

    Une question suivie d'une réponse qui ne garde pas la proposition la laisse
    derrière — la règle de #943 ne bouge pas, et c'est le juge, message par
    message, qui dit si la conversation porte encore sur elle.
    """
    muette = _message(NOM_ORCHESTRATION, "Deux runs tournent.")
    gardee = _message(NOM_ORCHESTRATION, "Environ 5 $.", proposition=OBJECTIF)

    assert proposition_en_attente(_fil(_message(UTILISATEUR, "et les runs ?"), muette)) is None
    assert proposition_en_attente(_fil(_message(UTILISATEUR, "combien ?"), gardee)) is gardee


# ── ③ une proposition porte son estimation ──────────────────────────────────────


def test_une_proposition_porte_l_estimation_des_taches_que_le_modele_compte() -> None:
    """Le modèle estime le nombre de tâches, le code chiffre avec les coûts de référence."""
    repondeur, _ = _repondeur(
        _dicte("Je peux lancer ça. Je lance ?", VERDICT_PROPOSITION, OBJECTIF, taches=5)
    )

    reponse = _produire(repondeur, [_message(UTILISATEUR, "Ajoute la pagination")])

    assert reponse.proposition == OBJECTIF
    assert reponse.estimation == EstimationRun(taches=5, bas_usd=4.5, haut_usd=9.9)


def test_sans_estimation_du_modele_c_est_le_plancher_et_il_le_dit() -> None:
    """Un modèle qui n'estime rien laisse le plancher — nommé tel, pas comme une estimation."""
    repondeur, _ = _repondeur(_dicte("Je lance ?", VERDICT_PROPOSITION, OBJECTIF))

    reponse = _produire(repondeur, [_message(UTILISATEUR, "Ajoute la pagination")])

    assert reponse.estimation is not None
    assert reponse.estimation.taches == NB_TACHES_PLANCHER
    assert reponse.estimation.estimees is False


@pytest.mark.parametrize("brut", [None, "", "beaucoup", True, 0, -3, float("nan")])
def test_un_compte_de_taches_illisible_vaut_le_plancher(brut: Any) -> None:
    estimation = estimer_run(brut)

    assert estimation.taches == NB_TACHES_PLANCHER and not estimation.estimees


def test_l_estimation_suit_la_methode_du_brief() -> None:
    """Découpage compris, marge de relance sur la borne haute seule — `estimerSuite`."""
    estimation = estimer_run("7")

    assert estimation.taches == 7 and estimation.estimees
    assert estimation.bas_usd == pytest.approx(0.8 + 7 * 0.74)
    assert estimation.haut_usd == pytest.approx(0.8 + 7 * 1.4 * 1.3)


def test_l_estimation_ne_borne_rien() -> None:
    """#494 ne bouge pas : un accord donné sur une estimation de 10 $ part sans plafond."""
    lanceur = LanceurEspion()
    repondeur, _ = _repondeur(_dicte("C'est parti.", VERDICT_ACCORD, OBJECTIF), lanceur=lanceur)

    _produire(repondeur, _fil(_message(UTILISATEUR, "oui")))

    assert lanceur.bornes == [AUCUNE_BORNE]


def test_estimation_et_bornes_voyagent_jusqu_au_message_et_se_relisent() -> None:
    """Persistées sur la ligne du fil : un fil rouvert demain montre la même carte."""
    message = _message(
        NOM_ORCHESTRATION,
        "C'est parti.",
        run_id="run-42",
        bornes=BornesRun(plafond_cout_usd=5.0),
        estimation=None,
    )
    propose = _proposition()

    assert MessageChat.from_dict(message.to_dict()).bornes == BornesRun(plafond_cout_usd=5.0)
    assert MessageChat.from_dict(propose.to_dict()).estimation == estimer_run(5)
    assert propose.to_dict()["estimation"] == {
        "taches": 5,
        "bas_usd": 4.5,
        "haut_usd": 9.9,
        "estimees": True,
    }


def test_une_ligne_ecrite_avant_ce_lot_se_relit_sans_estimation_ni_bornes() -> None:
    ancienne = {
        "agent": NOM_ORCHESTRATION,
        "auteur": NOM_ORCHESTRATION,
        "contenu": "Je lance ?",
        "proposition": OBJECTIF,
    }

    relue = MessageChat.from_dict(ancienne)

    assert relue.estimation is None and relue.bornes is None


#: Les constantes d'`estimation.ts`, lues dans le fichier : c'est la même estimation des
#: deux côtés, et deux jeux de chiffres feraient chiffrer le même travail deux fois.
_ESTIMATION_TS = Path(__file__).resolve().parents[1] / "apps" / "web" / "lib" / "estimation.ts"


def _constante_ts(nom: str) -> float:
    trouve = re.search(rf"export const {nom} = ([0-9.]+);", _ESTIMATION_TS.read_text("utf-8"))
    assert trouve is not None, f"{nom} introuvable dans {_ESTIMATION_TS.name}"
    return float(trouve.group(1))


def test_les_couts_de_reference_sont_les_memes_des_deux_cotes() -> None:
    """La carte d'une proposition et celle d'un brief chiffrent avec les mêmes nombres."""
    assert _constante_ts("COUT_DECOMPOSITION_USD") == COUT_DECOMPOSITION_USD
    assert _constante_ts("COUT_TACHE_USD_BAS") == COUT_TACHE_USD_BAS
    assert _constante_ts("COUT_TACHE_USD_HAUT") == COUT_TACHE_USD_HAUT
    assert _constante_ts("MARGE_RELANCES") == MARGE_RELANCES
    assert _constante_ts("NB_TACHES_PLANCHER") == NB_TACHES_PLANCHER
