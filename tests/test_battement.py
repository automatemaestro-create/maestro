"""Tests du battement de cœur d'un run (ticket #348, lot 1/4 de #347).

**Ni Redis, ni réseau, ni appel de modèle.** Le reste de la couverture du chantier
est différé au lot 4 (#351) ; ce qui est ici est la seule chose que ce lot ne
pouvait pas livrer sans test — le **calcul du verdict** et ses sources, exception
prévue par les notes techniques du ticket. La raison est de nature : le battement
n'a aucun effet observable en dehors de ce verdict, si bien qu'un dispositif qui
bat parfaitement et conclut de travers rendrait exactement l'écran d'avant, sans
que rien ne le signale.

Trois volets :

① **La règle** (`vitalite`) — vivant / orphelin / indéterminé, le seuil, le run
   soldé qui n'a pas de verdict, les attentes humaines qui en ont un, et les trois
   cas où l'on refuse de conclure à la mort (pas de battement, battement illisible,
   battement dans le futur).

② **La règle traverse l'API** — un run lancé est `vivant`, un battement vieilli le
   rend `orphelin`, un run soldé retombe à `null` (la question ne se pose plus),
   et un run qui n'a jamais battu reste `indetermine` au lieu d'être deviné.

③ **Un run publié hors de l'API** (`maestro-run --publier`, troisième critère) —
   reconnu vivant parce qu'il bat dans un registre partagé, **y compris sur une
   API redémarrée** : c'est le seul volet qui distingue ce dispositif de la
   déduction naïve que le ticket écarte (« l'API vient de démarrer, donc tout
   `en_cours` est mort »).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_TACHE_STATUT,
    VITALITE_INDETERMINE,
    VITALITE_ORPHELIN,
    VITALITE_VIVANT,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    RegistreBattementsMemoire,
    create_app,
    vitalite,
)
from maestro.controltower.battement import (
    SEUIL_ORPHELIN_S,
    CoeurRun,
    horodatage_battement,
)
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_ATTENTE_REPONSES,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
)
from maestro.engine import STATUT_TERMINEE

MAINTENANT = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


def _il_y_a(secondes: float) -> str:
    """L'horodatage d'un battement posé `secondes` avant `MAINTENANT`."""
    return horodatage_battement(MAINTENANT - timedelta(seconds=secondes))


# --------------------------------------------------------------- ① la règle


def test_un_run_qui_bat_est_vivant():
    """Le cas nominal : un battement récent, un hôte donc bien là."""
    assert (
        vitalite(EXECUTION_EN_COURS, _il_y_a(5), maintenant=MAINTENANT) == VITALITE_VIVANT
    )


def test_un_run_qui_ne_bat_plus_depuis_le_seuil_est_orphelin():
    """La panne du 2026-08-14 : l'hôte est tombé, le run restait `en_cours` à jamais."""
    assert (
        vitalite(EXECUTION_EN_COURS, _il_y_a(SEUIL_ORPHELIN_S + 1), maintenant=MAINTENANT)
        == VITALITE_ORPHELIN
    )


def test_le_seuil_est_inclusif_et_genereux():
    """Pile au seuil, le run est encore vivant — et le seuil est celui du ticket.

    La valeur est vérifiée ici et pas seulement sa mécanique : « généreux » est une
    exigence du ticket (une tâche d'agent peut travailler 26 min sans rien publier),
    et un seuil ramené à quelques minutes par inadvertance rendrait le dispositif
    faux dans le sens qui coûte cher — déclarer orphelin un run qui travaille.
    """
    assert (
        vitalite(EXECUTION_EN_COURS, _il_y_a(SEUIL_ORPHELIN_S), maintenant=MAINTENANT)
        == VITALITE_VIVANT
    )
    assert SEUIL_ORPHELIN_S >= 26 * 60


def test_un_run_qui_n_a_jamais_battu_est_indetermine():
    """Un run d'avant ce lot : on refuse de deviner, on le dit."""
    assert vitalite(EXECUTION_EN_COURS, None, maintenant=MAINTENANT) == VITALITE_INDETERMINE
    assert vitalite(EXECUTION_EN_COURS, "", maintenant=MAINTENANT) == VITALITE_INDETERMINE


def test_un_battement_illisible_ne_conclut_pas_a_la_mort():
    """Entrée corrompue : indéterminé, jamais orphelin — on n'affirme pas plus qu'on ne sait."""
    assert (
        vitalite(EXECUTION_EN_COURS, "hier après-midi", maintenant=MAINTENANT)
        == VITALITE_INDETERMINE
    )


def test_un_battement_dans_le_futur_reste_vivant():
    """Horloges désaccordées entre l'hôte et l'API : l'écart ne tue personne."""
    futur = horodatage_battement(MAINTENANT + timedelta(minutes=5))
    assert vitalite(EXECUTION_EN_COURS, futur, maintenant=MAINTENANT) == VITALITE_VIVANT


@pytest.mark.parametrize(
    "statut", [EXECUTION_TERMINEE, EXECUTION_ANNULEE, EXECUTION_ECHEC]
)
def test_un_run_solde_n_a_pas_de_verdict(statut):
    """La question ne se pose pas : un run qui a rendu son issue n'a plus d'hôte à guetter.

    Et elle ne se pose pas **même si le battement traîne encore** : c'est le statut
    qui tranche, pas le registre — sans quoi un run terminé dont l'entrée n'a pas
    pu être retirée (Redis muet une seconde) ressortirait orphelin.
    """
    assert vitalite(statut, _il_y_a(10_000), maintenant=MAINTENANT) is None
    assert vitalite(statut, _il_y_a(1), maintenant=MAINTENANT) is None


@pytest.mark.parametrize(
    "statut", [EXECUTION_EN_ATTENTE_BRIEF, EXECUTION_EN_ATTENTE_REPONSES]
)
def test_un_run_suspendu_sur_un_humain_garde_un_verdict(statut):
    """Attendre quelqu'un n'est pas être mort — et c'est là que la distinction sert le plus.

    Ces deux états ne sont pas terminaux : le run est en vol, simplement suspendu.
    Sans verdict, « personne n'a encore répondu » serait indiscernable de « le
    process qui posait la question est mort », c'est-à-dire des deux pertes de #347.
    """
    assert vitalite(statut, _il_y_a(5), maintenant=MAINTENANT) == VITALITE_VIVANT
    assert (
        vitalite(statut, _il_y_a(SEUIL_ORPHELIN_S + 1), maintenant=MAINTENANT)
        == VITALITE_ORPHELIN
    )


def test_le_registre_pose_relit_et_oublie():
    """Le contrat du registre, sur son implémentation mémoire."""

    async def scenario() -> None:
        registre = RegistreBattementsMemoire()
        assert await registre.battements() == {}
        await registre.battre("run-a", horodatage=_il_y_a(3))
        await registre.battre("run-b")
        assert set(await registre.battements()) == {"run-a", "run-b"}
        assert (await registre.battements())["run-a"] == _il_y_a(3)
        await registre.oublier("run-a")
        await registre.oublier("inconnu")  # sans effet, jamais une levée
        assert set(await registre.battements()) == {"run-b"}

    asyncio.run(scenario())


def test_le_coeur_bat_des_le_demarrage_puis_s_arrete_sans_rien_effacer():
    """`CoeurRun` : un premier battement **synchrone**, puis plus rien après l'arrêt.

    Le battement immédiat est ce qui empêche un run tout juste lancé d'être lu
    `indetermine` avant son premier tour d'horloge ; l'arrêt qui **n'efface pas**
    est ce qui le fera vieillir vers `orphelin` plutôt que redevenir inconnu.
    """
    battus: list[str] = []
    coeur = CoeurRun("run-cli", battus.append, periode=3600.0)

    coeur.demarrer()
    assert battus == ["run-cli"]  # posé avant même que le fil ne parte

    coeur.arreter()
    assert battus == ["run-cli"]


def test_un_battement_en_echec_ne_fait_pas_echouer_le_run():
    """Redis injoignable : le cœur trace et continue — il n'interrompt pas ce qu'il observe."""

    def tombe(_: str) -> None:
        raise RuntimeError("redis absent")

    coeur = CoeurRun("run-cli", tombe, periode=3600.0)
    coeur.demarrer()  # ne lève pas
    coeur.arreter()


# ------------------------------------------------- ② la règle traverse l'API


class MoteurEnVol:
    """Moteur injecté qui ne rend **jamais** la main : le run reste en cours.

    Deux raisons, et la seconde vaut pour toute la suite : sans injection, le
    premier lancement résoudrait un vrai fournisseur (`OrchestrationEngine
    .default`) et appellerait un modèle sur un poste aux clés renseignées, ce que
    `tests/conftest.py` (#195) interdit ; et un run qui se solderait pendant le
    test rendrait `vitalite: null` au lieu du verdict qu'on vient vérifier — la
    course serait tranchée par l'ordonnanceur, pas par le code.
    """

    def __init__(self, **_reglages: object) -> None:
        pass

    async def run(self, *_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()


def _app(
    battements: RegistreBattementsMemoire,
    journal: InMemoryEventLog | None = None,
):
    """L'app réelle sur bus mémoire, avec le registre de battements donné."""
    return create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        event_log=journal if journal is not None else InMemoryEventLog(),
        battements=battements,
        fabrique_moteur=MoteurEnVol,
    )


def _run_publie(run_id: str) -> Event:
    """L'étape d'un run publié **hors** de l'API : elle le fait exister, sans statut de run.

    C'est exactement ce que produit `maestro-run --publier` : des étapes de tâche
    portant un `run_id`, et aucun `execution.statut` — d'où un run que la
    projection tient pour `en_cours` sans jamais rien apprendre de sa fin.
    """
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=run_id,
        tache_id="t1",
        titre="Concevoir le schéma",
        agent="bdd",
        role="Base de données",
        statut=STATUT_TERMINEE,
    )


def test_un_run_lance_par_l_api_est_rendu_vivant():
    """Le lancement bat avant de rendre la main : le run n'est jamais lu `indetermine`."""
    battements = RegistreBattementsMemoire()
    with TestClient(_app(battements)) as client:
        lance = client.post("/api/executions", json={"objectif": "Prototyper"})
        assert lance.status_code == 202
        assert lance.json()["vitalite"] == VITALITE_VIVANT

        (resume,) = client.get("/api/executions?projet=tous").json()
        assert resume["vitalite"] == VITALITE_VIVANT


def test_un_run_dont_le_battement_a_vieilli_est_rendu_orphelin():
    """Les quatre runs fantômes du 2026-08-17, désormais nommés pour ce qu'ils sont."""
    battements = RegistreBattementsMemoire()
    journal = InMemoryEventLog()
    asyncio.run(journal.consigner(_run_publie("run-mort")))
    asyncio.run(
        battements.battre("run-mort", horodatage=_il_y_a(SEUIL_ORPHELIN_S + 60))
    )

    with TestClient(_app(battements, journal)) as client:
        (resume,) = client.get("/api/executions?projet=tous").json()

        assert resume["statut"] == EXECUTION_EN_COURS  # la projection ne sait rien de sa fin
        assert resume["vitalite"] == VITALITE_ORPHELIN


def test_un_run_sans_aucun_battement_reste_indetermine():
    """Un run d'avant ce lot n'est pas déclaré mort : rien ne l'a jamais écouté."""
    journal = InMemoryEventLog()
    asyncio.run(journal.consigner(_run_publie("run-ancien")))

    with TestClient(_app(RegistreBattementsMemoire(), journal)) as client:
        (resume,) = client.get("/api/executions?projet=tous").json()

        assert resume["vitalite"] == VITALITE_INDETERMINE


def test_le_detail_d_un_run_porte_le_meme_verdict_que_la_liste():
    """Une liste qui saurait qu'un run est orphelin pendant que son écran l'ignore
    serait une couture, pas une économie."""
    battements = RegistreBattementsMemoire()
    journal = InMemoryEventLog()
    asyncio.run(journal.consigner(_run_publie("run-mort")))
    asyncio.run(
        battements.battre("run-mort", horodatage=_il_y_a(SEUIL_ORPHELIN_S + 60))
    )

    with TestClient(_app(battements, journal)) as client:
        detail = client.get("/api/executions/run-mort").json()

        assert detail["vitalite"] == VITALITE_ORPHELIN


def test_un_run_annule_n_a_plus_de_verdict_et_son_battement_est_retire():
    """Soldé : `vitalite` retombe à `null`, et le registre ne garde pas l'entrée."""
    battements = RegistreBattementsMemoire()
    with TestClient(_app(battements)) as client:
        run_id = client.post("/api/executions", json={"objectif": "Prototyper"}).json()[
            "run_id"
        ]

        annule = client.post(f"/api/executions/{run_id}/annuler").json()

        assert annule["statut"] == EXECUTION_ANNULEE
        assert annule["vitalite"] is None
        assert run_id not in asyncio.run(battements.battements())


# --------------------------------------- ③ un run publié hors de l'API (critère 3)


def test_un_run_publie_hors_de_l_api_est_reconnu_vivant_apres_un_redemarrage():
    """Le troisième critère, et la raison d'être du dispositif.

    Un run lancé par `maestro-run --publier` vit dans **son** process : aucun
    redémarrage de l'API ne le concerne, et la déduction que le ticket écarte
    (« l'API vient de démarrer, donc tout `en_cours` est mort ») le tuerait à tort.
    Ici l'API repart à neuf — projection vide, journal rejoué — pendant que l'hôte,
    lui, continue de battre : le run ressort vivant, et c'est le battement seul qui
    le dit.
    """
    battements = RegistreBattementsMemoire()  # le Redis partagé, en mémoire
    journal = InMemoryEventLog()
    asyncio.run(journal.consigner(_run_publie("run-cli")))

    with TestClient(_app(battements, journal)) as premiere:
        asyncio.run(battements.battre("run-cli"))  # l'hôte bat
        (avant,) = premiere.get("/api/executions?projet=tous").json()
        assert avant["vitalite"] == VITALITE_VIVANT

    # L'API redémarre : nouvelle app, nouvelle projection, même journal et même
    # registre — exactement ce que retrouve `maestro-api` au redémarrage.
    with TestClient(_app(battements, journal)) as redemarree:
        (apres,) = redemarree.get("/api/executions?projet=tous").json()

        assert apres["statut"] == EXECUTION_EN_COURS
        assert apres["vitalite"] == VITALITE_VIVANT


def test_un_registre_muet_rend_indetermine_au_lieu_d_echouer():
    """Redis injoignable : la liste des runs reste servie — c'est justement ce
    qu'on regarde quand quelque chose ne va pas."""

    class RegistreEnPanne(RegistreBattementsMemoire):
        async def battements(self) -> dict[str, str]:
            raise RuntimeError("redis absent")

    journal = InMemoryEventLog()
    asyncio.run(journal.consigner(_run_publie("run-cli")))

    with TestClient(_app(RegistreEnPanne(), journal)) as client:
        reponse = client.get("/api/executions?projet=tous")

        assert reponse.status_code == 200
        (resume,) = reponse.json()
        assert resume["vitalite"] == VITALITE_INDETERMINE
