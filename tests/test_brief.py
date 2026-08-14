"""Brief structuré et validation humaine — la suite différée de la Phase 8 (#323).

Lot final de la Phase 8 : il porte les tests que #318 (le brief est rédigé) et
#320 (le run s'arrête dessus) ont différés. Les deux lots ont livré du code sans
suite dédiée — `tests/test_engine.py` et `tests/test_executions.py` ne
connaissaient du brief que le **nom du paramètre** qu'on leur passait.

Rien ici ne demande de backend ni de réseau ([docs/10 §8](../docs/10-workflow-git.md)) :
les appels modèle sont joués par des `ModelProvider` factices, le bus est un
`InMemoryEventBus`, et l'API est exercée par le `TestClient` de Starlette.

Six couches, de la valeur au contrat HTTP :

1. le **schéma partagé** (`packages/shared/schemas/brief.schema.json`) refuse ce
   qui n'est pas un brief, et le dit en un seul message ;
2. le **`Brief`** se relit sans se rejuger — aller-retour fidèle, synthèse
   Markdown dont une section vide **s'affiche** au lieu de disparaître ;
3. les **trois régimes** (`sans`/`auto`/`humain`) sont un ensemble fermé, refusé
   tôt ;
4. la **boucle** ne décompose que ce qui a été approuvé, et **rien** sur un refus ;
5. l'**arbitre Control Tower** attend la bonne décision, sur le bon run, et
   n'approuve jamais par défaut ;
6. la **projection** et les **routes** rendent l'attente pilotable — et refusent
   une décision qui n'a pas lieu d'être.

Ce qui n'est pas ici, et pourquoi : les **questions de clarification** (#321) ont
leur propre suite (`tests/test_clarifications.py`, livrée avec le lot) — ce
fichier ne couvre du sujet que ce qui existe côté brief, le champ `questions` et
son rendu.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.brief import (
    ACTEUR_BRIEF,
    ROLE_BRIEF,
    ArbitreBriefControlTower,
    arbitre_brief_redis,
    evenement_demande_brief,
)
from maestro.controltower.events import (
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_BRIEF_DEMANDE,
    EVENEMENT_EXECUTION_STATUT,
    Event,
    InMemoryEventBus,
    brief_depuis,
)
from maestro.controltower.state import (
    BRIEF_APPROUVE,
    BRIEF_REFUSE,
    EXECUTION_ANNULEE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    STATUTS_EXECUTION_TERMINAUX,
    ControlTowerState,
)
from maestro.engine import (
    MODE_BRIEF_AUTO,
    MODE_BRIEF_HUMAIN,
    MODE_BRIEF_SANS,
    MODES_BRIEF,
    BriefRefuse,
    DecisionBrief,
    DemandeBrief,
    OrchestrationEngine,
)
from maestro.engine.brief import mode_brief_valide
from maestro.orchestrator import (
    BRIEF_SYSTEM_PROMPT,
    Brief,
    BriefParsingError,
    BriefValidationError,
    Orchestrator,
    build_brief_user_prompt,
    validate_brief,
)
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal
from maestro.telemetry.costs import ETAPE_BRIEF

RUN = "b1e5f0000001"

#: Le plus petit brief conforme : les quatre clés requises, rien de plus.
BRIEF_MINIMAL: dict[str, Any] = {
    "objectif": "Prototyper un mini-CRM",
    "perimetre": ["Fiches contacts"],
    "hors_perimetre": [],
    "criteres_acceptation": ["Une fiche se crée et se relit"],
}

#: Un brief complet — les sept sections renseignées, questions comprises.
BRIEF_COMPLET: dict[str, Any] = {
    "objectif": "Prototyper un mini-CRM",
    "perimetre": ["Fiches contacts", "Recherche"],
    "hors_perimetre": ["Facturation"],
    "contraintes": ["Python 3.11"],
    "criteres_acceptation": ["Une fiche se crée et se relit", "La recherche répond"],
    "hypotheses": ["Une seule langue"],
    "questions": ["Faut-il un import CSV ?"],
}

_PLAN_JSON = json.dumps(
    [
        {
            "id": "fiches",
            "titre": "Fiches contacts",
            "description": "CRUD des contacts.",
            "competences_requises": ["backend", "api"],
            "format_sortie": "Module d'API",
            "dependances": [],
        }
    ],
    ensure_ascii=False,
)


def brief(**surcharges: Any) -> Brief:
    """Un `Brief` complet, éventuellement surchargé champ par champ."""
    donnees = {**BRIEF_COMPLET, **surcharges}
    return Brief.from_dict(donnees)


# --- Fournisseurs factices ------------------------------------------------------------


class ProviderCadrage(ModelProvider):
    """Rend un brief à l'étape de brief, un plan à la planification.

    Un seul `Orchestrator` porte les deux appels ; c'est le `system_prompt` qui
    les distingue — donc exactement ce que le vrai fournisseur voit. Enregistre
    les prompts pour que les tests vérifient **ce qui est réellement décomposé**.
    """

    name = "cadrage"

    def __init__(self, *, brief_json: str | None = None, plan_json: str | None = None) -> None:
        self._brief = (
            brief_json
            if brief_json is not None
            else json.dumps(BRIEF_COMPLET, ensure_ascii=False)
        )
        self._plan = plan_json if plan_json is not None else _PLAN_JSON
        self.prompts_brief: list[str] = []
        self.prompts_plan: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        if system_prompt == BRIEF_SYSTEM_PROMPT:
            self.prompts_brief.append(prompt)
            return self._brief
        self.prompts_plan.append(prompt)
        return self._plan


class ProviderExecution(ModelProvider):
    """Exécutant factice texte-seul : compte les tâches réellement exécutées."""

    name = "execution"

    def __init__(self) -> None:
        self.appels = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels += 1
        return f"LIVRABLE #{self.appels}"


def moteur(
    *,
    cadrage: ProviderCadrage | None = None,
    execution: ProviderExecution | None = None,
    arbitre=None,
) -> tuple[OrchestrationEngine, ProviderCadrage, ProviderExecution]:
    """Un moteur câblé sur des fournisseurs factices, avec ou sans arbitre."""
    cadrage = cadrage if cadrage is not None else ProviderCadrage()
    execution = execution if execution is not None else ProviderExecution()
    orchestrateur = Orchestrator(cadrage, model="claude-opus-4-8")
    return (
        OrchestrationEngine(execution, orchestrateur, arbitre_brief=arbitre),
        cadrage,
        execution,
    )


def arbitre_qui(decision: DecisionBrief, journal: list[DemandeBrief] | None = None):
    """Un arbitre coroutine qui rend toujours `decision` et note ce qu'on lui soumet."""

    async def _arbitre(demande: DemandeBrief) -> DecisionBrief:
        if journal is not None:
            journal.append(demande)
        return decision

    return _arbitre


# --- ① Le schéma partagé refuse ce qui n'est pas un brief -----------------------------


def test_le_brief_minimal_passe_la_validation():
    """Les quatre clés requises suffisent : le reste a un défaut vide."""
    assert validate_brief(BRIEF_MINIMAL) is None


def test_hors_perimetre_est_requis_mais_peut_etre_vide():
    """« Rien n'est hors périmètre » est une affirmation, pas une section absente."""
    assert validate_brief({**BRIEF_MINIMAL, "hors_perimetre": []}) is None
    with pytest.raises(BriefValidationError):
        validate_brief({k: v for k, v in BRIEF_MINIMAL.items() if k != "hors_perimetre"})


@pytest.mark.parametrize(
    ("surcharge", "attendu"),
    [
        ({"objectif": ""}, "objectif"),
        ({"perimetre": []}, "perimetre"),
        ({"criteres_acceptation": []}, "criteres_acceptation"),
        ({"perimetre": ["a", "a"]}, "perimetre"),
        ({"objectif": 42}, "objectif"),
    ],
)
def test_le_schema_refuse_un_brief_mal_forme(surcharge, attendu):
    """Objectif vide, listes vides là où le schéma exige du contenu, doublons, type."""
    with pytest.raises(BriefValidationError) as capture:
        validate_brief({**BRIEF_MINIMAL, **surcharge})
    assert attendu in str(capture.value)


def test_une_cle_inconnue_est_refusee():
    """`additionalProperties: false` : un brief n'accueille pas de champ inventé."""
    with pytest.raises(BriefValidationError) as capture:
        validate_brief({**BRIEF_MINIMAL, "priorite": "haute"})
    assert "priorite" in str(capture.value)


def test_tous_les_manquements_tiennent_dans_un_seul_message():
    """Un aller-retour par manquement serait N corrections pour un seul brief."""
    with pytest.raises(BriefValidationError) as capture:
        validate_brief({"objectif": "", "perimetre": [], "hors_perimetre": []})
    message = str(capture.value)
    assert "objectif" in message
    assert "perimetre" in message
    assert "criteres_acceptation" in message


def test_le_message_situe_l_erreur_et_nomme_son_contexte():
    """`where` distingue le brief proposé du brief corrigé rendu par l'humain."""
    with pytest.raises(BriefValidationError) as capture:
        validate_brief({**BRIEF_MINIMAL, "objectif": ""}, where="brief corrigé")
    assert str(capture.value).startswith("brief corrigé invalide :")


# --- ② Le Brief se relit sans se rejuger ----------------------------------------------


def test_aller_retour_dict_fidele():
    """`from_dict` → `to_dict` rend un dict qui repasse la validation à l'identique."""
    reconstruit = Brief.from_dict(BRIEF_COMPLET).to_dict()
    assert validate_brief(reconstruit) is None
    assert reconstruit == BRIEF_COMPLET


def test_les_sept_cles_sont_toujours_emises():
    """Aucune clé omise : l'écran de validation n'a pas à distinguer absent de vide."""
    emis = Brief.from_dict(BRIEF_MINIMAL).to_dict()
    assert set(emis) == {
        "objectif",
        "perimetre",
        "hors_perimetre",
        "contraintes",
        "criteres_acceptation",
        "hypotheses",
        "questions",
    }
    assert emis["contraintes"] == []
    assert emis["questions"] == []


def test_les_champs_multivalues_sont_immuables():
    """Tuples : un brief approuvé ne se réécrit pas dans le dos de qui l'a approuvé."""
    b = brief()
    assert isinstance(b.perimetre, tuple)
    with pytest.raises(AttributeError):
        b.objectif = "autre chose"  # type: ignore[misc]


def test_a_des_questions_dit_s_il_reste_des_zones_d_ombre():
    assert brief().a_des_questions is True
    assert brief(questions=[]).a_des_questions is False


def test_la_synthese_rend_les_sept_sections_dans_l_ordre_du_schema():
    """C'est le texte que l'humain relit pour approuver — donc son ordre compte."""
    rendu = brief().synthese()
    titres = [ligne for ligne in rendu.splitlines() if ligne.startswith("## ")]
    assert titres == [
        "## Périmètre",
        "## Hors périmètre",
        "## Contraintes",
        "## Critères d'acceptation",
        "## Hypothèses",
        "## Questions",
    ]
    assert rendu.startswith("# Brief\n\nPrototyper un mini-CRM\n")
    assert rendu.endswith("\n")
    assert "- Faut-il un import CSV ?" in rendu


def test_une_section_vide_s_affiche_au_lieu_de_disparaitre():
    """Un brief sans hors-périmètre est une information, pas une section manquante."""
    rendu = Brief.from_dict(BRIEF_MINIMAL).synthese()
    assert "## Hors périmètre\n\n- —\n" in rendu
    assert "## Contraintes\n\n- —\n" in rendu


# --- ③ Trois régimes, jamais devinés --------------------------------------------------


def test_les_trois_modes_sont_ordonnes_par_implication_humaine():
    assert MODES_BRIEF == (MODE_BRIEF_SANS, MODE_BRIEF_AUTO, MODE_BRIEF_HUMAIN)


@pytest.mark.parametrize("entree", [None, ""])
def test_ne_rien_dire_vaut_le_mode_sans(entree):
    """Un appelant muet ne se met pas à payer un appel modèle de plus."""
    assert mode_brief_valide(entree) == MODE_BRIEF_SANS


def test_un_mode_blanc_est_refuse_et_non_assimile_a_rien_dire():
    """`None`/`""` sont l'absence de choix ; des espaces sont un choix mal écrit.

    La nuance se teste parce qu'elle se lit mal dans le code (`mode or "sans"`
    laisse passer `"   "`, que le `.strip()` vide ensuite) : elle est du bon côté
    — un régime illisible est refusé, jamais deviné.
    """
    with pytest.raises(ValueError):
        mode_brief_valide("   ")


@pytest.mark.parametrize("entree", ["humain", "HUMAIN", "  Humain  "])
def test_le_mode_est_normalise(entree):
    assert mode_brief_valide(entree) == MODE_BRIEF_HUMAIN


def test_un_mode_inconnu_est_refuse_en_nommant_les_attendus():
    """Se tromper de mode, c'est payer un brief non voulu ou suspendre un run à vie."""
    with pytest.raises(ValueError) as capture:
        mode_brief_valide("manuel")
    message = str(capture.value)
    assert "manuel" in message
    assert "sans, auto, humain" in message


def test_la_decision_retient_le_brief_corrige_sinon_le_propose():
    """« Ce qui est décomposé est le brief tel qu'il a été approuvé » — un seul énoncé."""
    propose = brief()
    corrige = brief(objectif="Prototyper un mini-CRM, sans import CSV")
    assert DecisionBrief(approuve=True).retenu(propose) is propose
    assert DecisionBrief(approuve=True, brief=corrige).retenu(propose) is corrige


# --- ④ La boucle ne décompose que ce qui a été approuvé -------------------------------


def test_mode_sans_ne_paie_aucun_brief_et_decompose_l_objectif_brut():
    """Le comportement d'avant #320, et le défaut du moteur."""
    engine, cadrage, _ = moteur()
    rapport = asyncio.run(engine.run("Prototyper un mini-CRM"))
    assert cadrage.prompts_brief == []
    assert rapport.brief is None
    assert rapport.mode_brief == MODE_BRIEF_SANS
    assert cadrage.prompts_plan == ["Prototyper un mini-CRM"] or (
        "Prototyper un mini-CRM" in cadrage.prompts_plan[0]
    )
    assert "# Brief" not in cadrage.prompts_plan[0]


def test_mode_auto_redige_le_brief_et_n_attend_personne():
    """Un run headless qui attend une approbation est un run mort."""
    soumis: list[DemandeBrief] = []
    engine, cadrage, _ = moteur(
        arbitre=arbitre_qui(DecisionBrief(approuve=False, detail="refus"), soumis)
    )
    rapport = asyncio.run(engine.run("Prototyper un mini-CRM", mode_brief=MODE_BRIEF_AUTO))
    assert len(cadrage.prompts_brief) == 1
    assert soumis == []
    assert rapport.brief is not None
    assert rapport.mode_brief == MODE_BRIEF_AUTO


def test_c_est_la_synthese_du_brief_qui_part_en_decomposition():
    """Décomposer moins que ce qui a été approuvé rendrait l'approbation trompeuse."""
    engine, cadrage, _ = moteur()
    asyncio.run(engine.run("Prototyper un mini-CRM", mode_brief=MODE_BRIEF_AUTO))
    entree = cadrage.prompts_plan[0]
    assert "# Brief" in entree
    assert "## Critères d'acceptation" in entree
    assert "Facturation" in entree  # le hors-périmètre voyage avec le reste


def test_mode_humain_sans_arbitre_est_refuse_avant_tout_appel_modele():
    """Payer un brief pour se suspendre ensuite : le refus est gratuit et immédiat."""
    engine, cadrage, execution = moteur()
    with pytest.raises(ValueError) as capture:
        asyncio.run(engine.run("Prototyper un mini-CRM", mode_brief=MODE_BRIEF_HUMAIN))
    assert "arbitre" in str(capture.value)
    assert cadrage.prompts_brief == []
    assert cadrage.prompts_plan == []
    assert execution.appels == 0


def test_un_mode_inconnu_ne_coute_aucun_appel_modele():
    engine, cadrage, _ = moteur()
    with pytest.raises(ValueError):
        asyncio.run(engine.run("Prototyper un mini-CRM", mode_brief="manuel"))
    assert cadrage.prompts_brief == []
    assert cadrage.prompts_plan == []


def test_mode_humain_soumet_l_objectif_d_origine_avec_le_brief():
    """On n'approuve pas une reformulation sans son original."""
    soumis: list[DemandeBrief] = []
    engine, _, _ = moteur(arbitre=arbitre_qui(DecisionBrief(approuve=True), soumis))
    journal = RunJournal()
    asyncio.run(
        engine.run("Prototyper un mini-CRM", journal=journal, mode_brief=MODE_BRIEF_HUMAIN)
    )
    assert len(soumis) == 1
    assert soumis[0].objectif == "Prototyper un mini-CRM"
    assert soumis[0].run_id == journal.run_id
    assert soumis[0].brief.objectif == BRIEF_COMPLET["objectif"]


def test_un_brief_corrige_est_celui_qui_est_decompose_et_rapporte():
    """L'humain ne se contente pas d'approuver : il réécrit avant d'approuver."""
    corrige = brief(objectif="Prototyper un mini-CRM sans recherche", perimetre=["Fiches"])
    engine, cadrage, _ = moteur(
        arbitre=arbitre_qui(DecisionBrief(approuve=True, brief=corrige))
    )
    rapport = asyncio.run(
        engine.run("Prototyper un mini-CRM", mode_brief=MODE_BRIEF_HUMAIN)
    )
    assert rapport.brief == corrige
    assert "sans recherche" in cadrage.prompts_plan[0]


def test_un_refus_leve_avant_toute_tache():
    """La moitié gratuite du run (le brief) protège la moitié payante (les tâches)."""
    engine, cadrage, execution = moteur(
        arbitre=arbitre_qui(DecisionBrief(approuve=False, detail="périmètre trop large"))
    )
    with pytest.raises(BriefRefuse) as capture:
        asyncio.run(engine.run("Prototyper un mini-CRM", mode_brief=MODE_BRIEF_HUMAIN))
    assert "périmètre trop large" in str(capture.value)
    assert cadrage.prompts_plan == []
    assert execution.appels == 0


def test_un_refus_sans_motif_reste_explicite():
    engine, _, _ = moteur(arbitre=arbitre_qui(DecisionBrief(approuve=False)))
    with pytest.raises(BriefRefuse) as capture:
        asyncio.run(engine.run("Prototyper un mini-CRM", mode_brief=MODE_BRIEF_HUMAIN))
    assert "refusé" in str(capture.value)


def test_le_rapport_annonce_toujours_le_regime_du_brief():
    """Savoir qu'un run n'a attendu personne est une information, pas une omission."""
    engine, _, _ = moteur()
    rapport = asyncio.run(engine.run("Prototyper un mini-CRM"))
    assert "mode « sans »" in rapport.resume_brief()
    assert "Brief : " in rapport.synthese()
    assert rapport.to_dict()["mode_brief"] == MODE_BRIEF_SANS
    assert rapport.to_dict()["brief"] is None


def test_le_rapport_porte_le_brief_retenu_et_son_cout():
    """Sans cela, le brief serait la seule dépense du run à ne figurer nulle part."""
    engine, _, _ = moteur(arbitre=arbitre_qui(DecisionBrief(approuve=True)))
    rapport = asyncio.run(
        engine.run("Prototyper un mini-CRM", mode_brief=MODE_BRIEF_HUMAIN)
    )
    assert "mode « humain »" in rapport.resume_brief()
    assert rapport.to_dict()["brief"]["objectif"] == BRIEF_COMPLET["objectif"]
    assert rapport.grand_livre.brief == rapport.cadrage


def test_l_etape_de_brief_est_consignee_au_journal():
    """Le cadrage est une étape du run, traçable comme la planification (#8)."""
    engine, _, _ = moteur()
    journal = RunJournal()
    asyncio.run(
        engine.run("Prototyper un mini-CRM", journal=journal, mode_brief=MODE_BRIEF_AUTO)
    )
    etapes = [ligne for ligne in journal.records if ligne.etape == ETAPE_BRIEF]
    assert len(etapes) == 1
    assert etapes[0].agent == "orchestrateur"
    assert etapes[0].statut == "terminee"
    assert "2 critère(s) d'acceptation" in etapes[0].sortie
    assert "1 question(s)" in etapes[0].sortie


def test_un_brief_illisible_est_consigne_puis_remonte():
    """L'échec du cadrage n'est pas silencieux : il laisse sa ligne avant de lever."""
    engine, _, _ = moteur(cadrage=ProviderCadrage(brief_json="je n'ai pas compris"))
    journal = RunJournal()
    with pytest.raises(BriefParsingError):
        asyncio.run(
            engine.run("Prototyper un mini-CRM", journal=journal, mode_brief=MODE_BRIEF_AUTO)
        )
    etapes = [ligne for ligne in journal.records if ligne.etape == ETAPE_BRIEF]
    assert len(etapes) == 1
    assert etapes[0].statut == "echec"


def test_un_brief_hors_schema_est_refuse_par_l_orchestrateur():
    """Le modèle ne fixe pas le contrat : le schéma partagé le fait."""
    hors_schema = json.dumps({"objectif": "", "perimetre": [], "hors_perimetre": []})
    engine, _, _ = moteur(cadrage=ProviderCadrage(brief_json=hors_schema))
    with pytest.raises(BriefValidationError):
        asyncio.run(engine.run("Prototyper un mini-CRM", mode_brief=MODE_BRIEF_AUTO))


def test_les_sources_entrent_par_le_prompt_en_dernier_et_jamais_comme_consigne():
    """Sans source, le prompt le dit — il n'invente pas de document (ENF-13)."""
    sans = build_brief_user_prompt("Prototyper un mini-CRM")
    assert "Aucune source n'a été fournie" in sans
    avec = build_brief_user_prompt("Prototyper un mini-CRM", "## Source lue\n\ncontenu")
    assert avec.rstrip().endswith("## Source lue\n\ncontenu")
    assert "Aucune source" not in avec


# --- ⑤ L'arbitre Control Tower --------------------------------------------------------


def test_la_demande_expurge_l_objectif_mais_jamais_le_brief():
    """Approuver une version masquée d'un texte qui s'exécutera en clair n'a pas de sens."""
    demande = DemandeBrief(
        run_id=RUN,
        objectif="Déployer avec sk-ant-api03-secretsecretsecretsecretsecret",
        brief=brief(),
    )
    event = evenement_demande_brief(demande)
    assert event.type == EVENEMENT_BRIEF_DEMANDE
    assert event.run_id == RUN
    assert "sk-ant-api03-secretsecretsecretsecretsecret" not in event.titre
    assert event.agent == ACTEUR_BRIEF
    assert event.role == ROLE_BRIEF
    assert event.statut == EXECUTION_EN_ATTENTE_BRIEF
    assert event.mode_brief == MODE_BRIEF_HUMAIN
    assert event.brief == demande.brief


async def _laisse_l_arbitre_s_abonner() -> None:
    """Rend la main assez de tours pour que l'abonnement soit posé.

    `InMemoryEventBus.subscribe` est un générateur asynchrone : sa file n'existe
    qu'au premier `__anext__`. Publier avant, c'est publier dans le vide — le
    contre-test de cette précaution est `test_l_arbitre_ignore_le_reste_du_bus`,
    qui vérifie que l'arbitre voit bien passer ce qu'il doit ignorer.
    """
    for _ in range(5):
        await asyncio.sleep(0)


def _decide(bus: InMemoryEventBus, **surcharges: Any) -> DecisionBrief:
    """Fait tourner l'arbitre et publie une décision dès la demande vue."""

    async def _scenario() -> DecisionBrief:
        arbitre = ArbitreBriefControlTower(bus)
        attente = asyncio.create_task(
            arbitre(DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief()))
        )
        await _laisse_l_arbitre_s_abonner()
        await bus.publish(
            Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, **surcharges)
        )
        # Borné : l'attente de l'arbitre est indéfinie **par contrat**, donc une
        # régression du filtrage bloquerait la suite entière au lieu d'échouer.
        return await asyncio.wait_for(attente, timeout=5)

    return asyncio.run(_scenario())


def test_l_arbitre_rend_l_approbation_publiee_sur_le_bus():
    decision = _decide(InMemoryEventBus(), statut=BRIEF_APPROUVE, detail="ok")
    assert decision.approuve is True
    assert decision.detail == "ok"


def test_l_arbitre_transporte_le_brief_corrige():
    corrige = brief(objectif="Version relue")
    decision = _decide(InMemoryEventBus(), statut=BRIEF_APPROUVE, brief=corrige)
    assert decision.brief == corrige


def test_tout_statut_qui_n_est_pas_une_approbation_est_un_refus():
    """Fail-safe dans le bon sens : jamais d'approbation par défaut."""
    assert _decide(InMemoryEventBus(), statut=BRIEF_REFUSE).approuve is False
    assert _decide(InMemoryEventBus(), statut="").approuve is False


def test_l_arbitre_ignore_le_reste_du_bus():
    """Statuts de tâches et décisions visant un autre run ne réveillent personne."""
    bus = InMemoryEventBus()

    async def _scenario() -> DecisionBrief:
        arbitre = ArbitreBriefControlTower(bus)
        attente = asyncio.create_task(
            arbitre(DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief()))
        )
        await _laisse_l_arbitre_s_abonner()
        await bus.publish(Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN))
        await bus.publish(
            Event(type=EVENEMENT_BRIEF_DECISION, run_id="un-autre", statut=BRIEF_APPROUVE)
        )
        await _laisse_l_arbitre_s_abonner()
        assert not attente.done()
        await bus.publish(
            Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=BRIEF_REFUSE)
        )
        return await asyncio.wait_for(attente, timeout=5)

    assert asyncio.run(_scenario()).approuve is False


class BusQuiSeReferme(InMemoryEventBus):
    """Un bus dont le flux se **tarit** : personne ne tranchera jamais.

    `InMemoryEventBus.close()` est un no-op assumé (seul un bus à connexions a
    quelque chose à libérer), donc il ne peut pas jouer ce cas : c'est la fin de
    l'itération qu'il faut simuler, pas la fermeture d'une ressource.
    """

    async def subscribe(self):
        return
        yield  # pragma: no cover - fait de `subscribe` un générateur asynchrone


def test_un_bus_referme_sans_decision_leve_au_lieu_d_approuver():
    """Le run remonte l'erreur en échec, sans avoir décomposé."""

    async def _scenario() -> DecisionBrief:
        arbitre = ArbitreBriefControlTower(BusQuiSeReferme())
        return await arbitre(
            DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief())
        )

    with pytest.raises(RuntimeError) as capture:
        asyncio.run(_scenario())
    assert RUN in str(capture.value)


class BusQuiCompteSesAbonnes(InMemoryEventBus):
    """Un bus qui sait combien d'abonnements sont **encore ouverts**.

    Une attente indéfinie ne se solde pas toujours par une décision : le run
    peut être annulé, le bus tomber. L'abonnement doit alors partir avec elle —
    sinon un run suspendu sur son brief puis annulé laisse derrière lui une
    tâche qui écoute pour toujours, et rien dans l'API ne le montrerait. Le
    compteur est le seul point d'observation : la fuite est invisible du dehors.
    """

    def __init__(self) -> None:
        super().__init__()
        self.abonnements_ouverts = 0

    async def subscribe(self):
        self.abonnements_ouverts += 1
        try:
            async for event in super().subscribe():
                yield event
        finally:
            self.abonnements_ouverts -= 1


class BusQuiRefusePublier(BusQuiCompteSesAbonnes):
    """Un bus indisponible au moment de publier la demande."""

    async def publish(self, event: Event) -> None:
        raise RuntimeError("bus indisponible")


def test_un_run_annule_pendant_l_attente_referme_son_abonnement():
    """L'annulation d'un run suspendu sur son brief n'y laisse pas un écouteur."""
    bus = BusQuiCompteSesAbonnes()

    async def _scenario() -> int:
        arbitre = ArbitreBriefControlTower(bus)
        attente = asyncio.create_task(
            arbitre(DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief()))
        )
        await _laisse_l_arbitre_s_abonner()
        assert bus.abonnements_ouverts == 1
        attente.cancel()
        with suppress(asyncio.CancelledError):
            await attente
        return bus.abonnements_ouverts

    assert asyncio.run(_scenario()) == 0


def test_un_bus_indisponible_ne_laisse_pas_l_abonnement_derriere_lui():
    """La demande n'est jamais partie : l'écoute qui l'attendait n'a plus de raison d'être."""
    bus = BusQuiRefusePublier()

    async def _scenario() -> int:
        arbitre = ArbitreBriefControlTower(bus)
        with pytest.raises(RuntimeError):
            await arbitre(DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief()))
        return bus.abonnements_ouverts

    assert asyncio.run(_scenario()) == 0


def test_l_arbitre_de_production_ne_se_connecte_pas_en_le_construisant():
    """`arbitre_brief_redis` monte le bus Redis **sans ouvrir de connexion**.

    La connexion est paresseuse (`RedisEventBus`), et c'est ce qui permet à
    `maestro-run --publier --brief humain` de se construire son arbitre sans
    exiger un Redis joignable à l'import. Le test le vérifie du seul endroit où
    ça se voit : ici, où aucun réseau n'est disponible (`tests/conftest.py`).
    """
    arbitre = arbitre_brief_redis("redis://localhost:6379/0")
    assert isinstance(arbitre, ArbitreBriefControlTower)


# --- ⑥ Projection et contrat HTTP -----------------------------------------------------


def test_brief_depuis_relit_sans_rejuger():
    """Un journal durable rejoué après un durcissement du schéma reste lisible."""
    assert brief_depuis(BRIEF_COMPLET) == brief()
    assert brief_depuis(None) is None
    assert brief_depuis("pas un mapping") is None
    assert brief_depuis({"objectif": "seul"}) is None


def test_l_evenement_de_brief_fait_un_aller_retour_fidele():
    event = evenement_demande_brief(
        DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief())
    )
    assert Event.from_dict(event.to_dict()).brief == brief()


def _etat_en_attente() -> ControlTowerState:
    """Une projection portant un run suspendu sur son brief."""
    etat = ControlTowerState()
    etat.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            titre="Objectif",
            statut=EXECUTION_EN_COURS,
        )
    )
    etat.appliquer(
        evenement_demande_brief(DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief()))
    )
    return etat


def test_la_demande_suspend_le_run_dans_la_projection():
    execution = _etat_en_attente().execution(RUN)
    assert execution is not None
    assert execution.statut == EXECUTION_EN_ATTENTE_BRIEF
    assert execution.brief == brief()
    assert execution.mode_brief == MODE_BRIEF_HUMAIN
    assert execution.fin is None


def test_un_run_suspendu_n_est_pas_solde():
    """Il reste annulable : suspendre n'est pas terminer."""
    assert EXECUTION_EN_ATTENTE_BRIEF not in STATUTS_EXECUTION_TERMINAUX


def test_le_resume_reste_leger_mais_le_detail_porte_le_brief():
    execution = _etat_en_attente().execution(RUN)
    assert execution is not None
    assert "brief" not in execution.resume()
    assert execution.resume()["mode_brief"] == MODE_BRIEF_HUMAIN
    assert execution.to_dict()["brief"]["objectif"] == BRIEF_COMPLET["objectif"]


def test_une_approbation_remet_le_run_en_vol_un_refus_l_annule():
    issues = ((BRIEF_APPROUVE, EXECUTION_EN_COURS), (BRIEF_REFUSE, EXECUTION_ANNULEE))
    for statut, attendu in issues:
        etat = _etat_en_attente()
        etat.appliquer(Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=statut))
        execution = etat.execution(RUN)
        assert execution is not None
        assert execution.statut == attendu
    assert execution is not None
    assert execution.fin is not None


def test_une_seconde_decision_ne_ressuscite_rien():
    """Jamais deux décisions, jamais un run soldé ramené en vol."""
    etat = _etat_en_attente()
    etat.appliquer(Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=BRIEF_REFUSE))
    etat.appliquer(Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=BRIEF_APPROUVE))
    execution = etat.execution(RUN)
    assert execution is not None
    assert execution.statut == EXECUTION_ANNULEE


def test_une_decision_sur_un_run_qui_n_attend_pas_est_ignoree():
    etat = ControlTowerState()
    etat.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_TERMINEE)
    )
    etat.appliquer(Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=BRIEF_APPROUVE))
    execution = etat.execution(RUN)
    assert execution is not None
    assert execution.statut == EXECUTION_TERMINEE


@pytest.fixture()
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture()
def etat() -> ControlTowerState:
    return _etat_en_attente()


@pytest.fixture()
def client(bus, etat):
    """TestClient de l'app sur bus mémoire, avec un run déjà suspendu sur son brief."""
    with TestClient(create_app(bus=bus, state=etat)) as client:
        yield client


def test_la_decision_approuve_le_brief_et_remet_le_run_en_vol(client):
    reponse = client.post(f"/api/executions/{RUN}/brief/decision", json={"approuve": True})
    assert reponse.status_code == 200
    assert reponse.json()["statut"] == EXECUTION_EN_COURS


def test_la_decision_refuse_annule_le_run(client):
    reponse = client.post(f"/api/executions/{RUN}/brief/decision", json={"approuve": False})
    assert reponse.status_code == 200
    assert reponse.json()["statut"] == EXECUTION_ANNULEE


def test_un_run_inconnu_rend_404(client):
    reponse = client.post("/api/executions/inconnu/brief/decision", json={"approuve": True})
    assert reponse.status_code == 404
    assert "inconnu" in reponse.json()["detail"]


def test_un_run_qui_n_attend_pas_rend_409(client, etat):
    client.post(f"/api/executions/{RUN}/brief/decision", json={"approuve": True})
    reponse = client.post(f"/api/executions/{RUN}/brief/decision", json={"approuve": True})
    assert reponse.status_code == 409
    assert RUN in reponse.json()["detail"]


def test_un_brief_corrige_invalide_rend_422_et_ne_decide_rien(client, etat):
    reponse = client.post(
        f"/api/executions/{RUN}/brief/decision",
        json={"approuve": True, "brief": {**BRIEF_MINIMAL, "objectif": ""}},
    )
    assert reponse.status_code == 422
    assert "brief corrigé" in reponse.json()["detail"]
    execution = etat.execution(RUN)
    assert execution is not None
    assert execution.statut == EXECUTION_EN_ATTENTE_BRIEF


def test_un_brief_corrige_valide_remplace_le_propose(client, etat):
    corrige = {**BRIEF_MINIMAL, "objectif": "Version relue par un humain"}
    reponse = client.post(
        f"/api/executions/{RUN}/brief/decision", json={"approuve": True, "brief": corrige}
    )
    assert reponse.status_code == 200
    execution = etat.execution(RUN)
    assert execution is not None
    assert execution.brief is not None
    assert execution.brief.objectif == "Version relue par un humain"


def test_un_refus_n_emporte_jamais_de_brief(client, etat):
    """Sur un refus, le corps `brief` est ignoré — rien ne sera décomposé."""
    reponse = client.post(
        f"/api/executions/{RUN}/brief/decision",
        json={"approuve": False, "brief": {"objectif": ""}},
    )
    assert reponse.status_code == 200
    execution = etat.execution(RUN)
    assert execution is not None
    assert execution.brief == brief()


def test_le_detail_d_un_run_sert_le_brief(client):
    detail = client.get(f"/api/executions/{RUN}").json()
    assert detail["statut"] == EXECUTION_EN_ATTENTE_BRIEF
    assert detail["brief"]["objectif"] == BRIEF_COMPLET["objectif"]
    assert detail["mode_brief"] == MODE_BRIEF_HUMAIN


def test_un_mode_de_brief_inconnu_est_refuse_au_lancement(client):
    """Un mode mal orthographié coûte un 422, pas un run suspendu pour toujours."""
    reponse = client.post(
        "/api/executions", json={"objectif": "Prototyper un mini-CRM", "brief": "manuel"}
    )
    assert reponse.status_code == 422
    detail = reponse.json()["detail"]
    # Convention des refus (#315, #223) : un motif stable, jamais une phrase à
    # analyser — et le message nomme la valeur fautive, sans quoi corriger la
    # requête demanderait de deviner lequel des champs a été refusé.
    assert detail["motif"] == "requete-invalide"
    assert "manuel" in detail["message"]
    assert "sans, auto, humain" in detail["message"]
