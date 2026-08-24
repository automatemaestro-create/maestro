"""Le canal humain d'un hôte de run détaché (#445, lot 4 du chantier #441).

**Les tests du chantier sont différés au lot 6 (#447)** — l'hôte détaché n'a
aujourd'hui aucune suite, et ce fichier n'en est pas une. Il ne couvre que ce que
le ticket désigne comme « la partie qui compte » : le **fail-safe**. Un bus qui se
referme sans décision doit faire échouer le run ou refuser l'action, jamais rendre
une approbation par défaut — et ce parti pris, hérité, devient plus exposé quand
l'écouteur vit dans un autre process que le publieur.

Ce qui est vérifié ici est donc le **câblage**, et lui seul : que les trois
attentes humaines soient branchées sur le bus de ce process, et que leur absence
de bus retombe sur les refus qu'on attend. Le comportement des arbitres eux-mêmes
est déjà couvert (`tests/test_brief.py`, `tests/test_clarifications.py`,
`tests/test_controltower.py`) et n'est pas rejoué : ce qui manquait n'était pas
leur logique, c'était qu'on les leur passe.

Rien ici ne demande de backend, de réseau ni de process
([docs/10 §8](../docs/10-workflow-git.md)) : le bus est un `InMemoryEventBus`, le
moteur un double qui retient ce qu'on lui câble, et le seul `Popen` du module est
remplacé par un faux qui ne meurt pas.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from maestro.controltower import hote_detache
from maestro.controltower.brief import (
    ArbitreBriefControlTower,
    ArbitreClarificationControlTower,
)
from maestro.controltower.events import InMemoryEventBus
from maestro.controltower.hote import OrdreRun
from maestro.controltower.hote_detache import HoteRunDetache
from maestro.controltower.validation import ValidateurControlTower
from maestro.engine.brief import (
    MODE_BRIEF_AUTO,
    MODE_BRIEF_HUMAIN,
    DemandeBrief,
    DemandeClarification,
)
from maestro.engine.guardrails import DemandeValidation
from maestro.orchestrator.schema import Brief

RUN = "run-detache-445"


def brief() -> Brief:
    """Le brief minimal que les deux arbitres transportent."""
    return Brief(
        objectif="Objectif",
        perimetre=("dedans",),
        hors_perimetre=("dehors",),
        criteres_acceptation=("fait",),
        questions=("laquelle ?",),
    )


def ordre(**surcharges: Any) -> OrdreRun:
    """Un ordre de run, en mode « humain » sauf mention contraire."""
    champs: dict[str, Any] = {
        "run_id": RUN,
        "objectif": "Objectif",
        "mode_brief": MODE_BRIEF_HUMAIN,
    }
    champs.update(surcharges)
    return OrdreRun(**champs)


class BusQuiSeReferme(InMemoryEventBus):
    """Un bus dont le flux se **tarit** : personne ne tranchera jamais.

    Même double que `tests/test_brief.py`, et pour la même raison :
    `InMemoryEventBus.close()` est un no-op assumé (seul un bus à connexions a
    quelque chose à libérer), donc c'est la fin de l'itération qu'il faut jouer,
    pas la fermeture d'une ressource.
    """

    async def subscribe(self):  # type: ignore[override]
        return
        yield  # pragma: no cover - fait de `subscribe` un générateur asynchrone


class MoteurCapture:
    """Un faux `OrchestrationEngine` qui retient ce qu'on lui a câblé.

    Le seul moyen d'observer le câblage sans lancer de run : `_derouler` construit
    le moteur puis part, et rien de ce qu'il lui passe ne ressort autrement.
    """

    cable: dict[str, Any] = {}

    @classmethod
    def default(cls, **kwargs: Any) -> MoteurCapture:
        cls.cable = dict(kwargs)
        return cls()

    async def run(self, objectif: str, **kwargs: Any) -> str:
        cls = type(self)
        cls.cable["run"] = {"objectif": objectif, **kwargs}
        return "rapport"


def deroule(monkeypatch: pytest.MonkeyPatch, bus: Any, atelier: Path, **ordre_kw: Any):
    """Joue `_derouler` avec `bus` pour bus du process, et rend ce qui a été câblé."""
    import maestro.engine.loop as loop

    MoteurCapture.cable = {}
    monkeypatch.setattr(loop, "OrchestrationEngine", MoteurCapture)
    monkeypatch.setattr(hote_detache, "_bus_du_run", lambda: bus)
    asyncio.run(hote_detache._derouler(ordre(**ordre_kw), atelier))
    return MoteurCapture.cable


# --- Le câblage : les trois attentes tiennent sur le bus de ce process ---------


def test_les_trois_attentes_humaines_sont_cablees_sur_le_bus_du_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Brief, clarifications et action sensible reçoivent leur arbitre (#445).

    C'est le lot entier en un test : avant lui, `_derouler` construisait un moteur
    sans arbitre et des garde-fous sans validateur, si bien qu'un run détaché ne
    pouvait poser aucune des trois questions.
    """
    cable = deroule(monkeypatch, InMemoryEventBus(), tmp_path)

    assert isinstance(cable["arbitre_brief"], ArbitreBriefControlTower)
    assert isinstance(cable["arbitre_clarification"], ArbitreClarificationControlTower)
    assert isinstance(cable["guardrails"].validateur, ValidateurControlTower)


def test_le_meme_bus_sert_le_guet_et_les_trois_arbitres(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un process, un bus : quatre abonnements tiennent sur une connexion.

    Le contrôle porte sur l'**identité** de l'objet et pas sur son type : trois
    fabriques `*_redis` rendraient trois bus corrects, donc trois connexions dans
    un process qui vit des heures.
    """
    bus = InMemoryEventBus()
    cable = deroule(monkeypatch, bus, tmp_path)

    assert cable["arbitre_brief"]._bus is bus
    assert cable["arbitre_clarification"]._bus is bus
    assert cable["guardrails"].validateur._bus is bus


def test_le_mode_du_brief_voyage_avec_l_ordre_et_l_arbitre_reste_cable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le **mode** part avec l'objectif, l'**arbitre** est branché quoi qu'il arrive.

    Aucun des trois câblages n'est conditionné au mode : le moteur ignore de
    lui-même un arbitre qu'il n'a pas à consulter, et une seconde règle ici serait
    une règle de plus à tenir d'accord avec la sienne.
    """
    cable = deroule(monkeypatch, InMemoryEventBus(), tmp_path, mode_brief=MODE_BRIEF_AUTO)

    assert cable["run"]["mode_brief"] == MODE_BRIEF_AUTO
    assert isinstance(cable["arbitre_brief"], ArbitreBriefControlTower)


# --- Le fail-safe : jamais d'approbation par défaut ----------------------------


def test_un_bus_referme_sans_decision_fait_lever_le_brief_et_les_clarifications(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le run **échoue** — il ne repart pas avec un brief que personne n'a validé.

    Éprouvé sur les arbitres tels que l'hôte les a câblés, et non sur des arbitres
    montés pour l'occasion : ce que ce lot pouvait casser, c'est le branchement.
    """
    cable = deroule(monkeypatch, BusQuiSeReferme(), tmp_path)

    with pytest.raises(RuntimeError) as decision:
        asyncio.run(
            cable["arbitre_brief"](
                DemandeBrief(run_id=RUN, objectif="Objectif", brief=brief())
            )
        )
    assert RUN in str(decision.value)

    with pytest.raises(RuntimeError) as reponses:
        asyncio.run(
            cable["arbitre_clarification"](
                DemandeClarification(
                    run_id=RUN, objectif="Objectif", brief=brief(), tour=1, tours_max=2
                )
            )
        )
    assert RUN in str(reponses.value)


def test_un_bus_referme_sans_decision_fait_refuser_l_action_sensible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """La tâche est **soldée refusée**, comme sans validateur du tout (#9).

    L'asymétrie avec le test précédent est celle du dépôt et non un oubli : le
    brief refusé arrête le run avant qu'aucune tâche n'existe, l'action sensible
    refusée n'arrête que la tâche.
    """
    cable = deroule(monkeypatch, BusQuiSeReferme(), tmp_path)
    demande = DemandeValidation(
        task_id="t1",
        titre="Déployer en production",
        description="…",
        agent="dev",
        role="Développeur",
        raison="deploi",
    )

    approuve, detail = asyncio.run(cable["guardrails"].demande_validation(demande))

    assert approuve is False
    assert "refus par défaut" in detail


def test_sans_bus_rien_n_est_cable_et_les_deux_fail_safes_prennent_la_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le seul cas neuf du lot : un bus qu'on n'a pas pu **construire**.

    On ne lui invente pas un troisième refus — les deux existants suffisent, et
    c'est ce que ce test fixe : sans arbitre le moteur refusera le mode « humain »
    avant le premier appel modèle, sans validateur les garde-fous refusent toute
    action sensible.
    """
    cable = deroule(monkeypatch, None, tmp_path)

    assert cable["arbitre_brief"] is None
    assert cable["arbitre_clarification"] is None
    assert cable["guardrails"].validateur is None

    demande = DemandeValidation(
        task_id="t1",
        titre="Supprimer la base",
        description="…",
        agent="bdd",
        role="Base de données",
        raison="supprim",
    )
    approuve, detail = asyncio.run(cable["guardrails"].demande_validation(demande))
    assert approuve is False
    assert "aucun validateur humain configuré" in detail


def test_ouvrir_le_bus_ne_leve_jamais(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_bus_du_run` rend None au lieu de lever — sinon le run mourrait du bus.

    L'ouverture vivait **dans** le `try` de `_observer_annulation`, précisément
    pour qu'aucune façon de manquer Redis ne puisse emporter le run. La sortir de
    là sans reprendre la promesse aurait rendu fatal ce qui ne l'était pas.
    """

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("redis absent")

    monkeypatch.setattr(hote_detache, "RedisEventBus", refuse)

    assert hote_detache._bus_du_run() is None


# --- Le lancement : plus aucun mode de brief n'est refusé au départ ------------


class FauxProcess:
    """Un process qui vit : ni témoin posé, ni mort — le cas « parti mais lent »."""

    pid = 4321

    def poll(self) -> int | None:
        return None


def test_lancer_ne_refuse_plus_le_brief_humain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le refus de #443 a disparu, et l'ordre part avec son mode intact.

    Il existait parce que la décision n'avait aucun canal jusqu'au process ; elle
    en a un, donc le laisser en place refuserait le mode par **défaut** des
    lancements Control Tower pour une raison qui n'est plus vraie.
    """
    monkeypatch.setattr(
        HoteRunDetache, "_ouvrir_process", lambda self, atelier, journal: FauxProcess()
    )
    hote = HoteRunDetache(atelier=tmp_path, delai_demarrage_s=0.05)

    asyncio.run(hote.lancer(ordre()))

    ecrit = json.loads((tmp_path / RUN / hote_detache.FICHIER_ORDRE).read_text("utf-8"))
    assert ecrit["mode_brief"] == MODE_BRIEF_HUMAIN
    assert hote.en_vol(RUN) is True
