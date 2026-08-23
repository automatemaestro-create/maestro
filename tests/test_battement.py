"""Tests du battement de cœur d'un run (#348, lot 1/4 de #347 ; complété par #351).

**Ni Redis, ni réseau, ni appel de modèle.** Les trois premiers volets sont ceux
que #348 ne pouvait pas livrer sans test — le **calcul du verdict** et ses sources,
exception prévue par les notes techniques du ticket. La raison est de nature : le
battement n'a aucun effet observable en dehors de ce verdict, si bien qu'un
dispositif qui bat parfaitement et conclut de travers rendrait exactement l'écran
d'avant, sans que rien ne le signale.

Les volets ④ et ⑤ sont la part **différée au lot 4** (#351) : ce qui, au-delà de la
règle, fait que le dispositif bat vraiment — les deux hôtes, et ce qu'ils laissent
derrière eux en tombant.

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

④ **Les deux hôtes battent au même endroit** (#351) — ③ montre la règle sur un
   registre *partagé par construction* (le même objet mémoire). Ici on vérifie ce
   qui rend ce partage vrai en production : `maestro-run --publier` câble bien un
   cœur, du début du run à sa fin, et il écrit dans le **hash que l'API relit**.
   Une clé divergente entre les deux moitiés donnerait exactement l'écran d'avant —
   un run vivant, déclaré orphelin, sans que rien ne dise pourquoi.

⑤ **Ce que la mort de l'hôte laisse derrière elle** (#351) — le cœur du service ne
   bat que pour ce qui est en vol, et l'arrêt de l'API **n'efface rien** : c'est ce
   dernier point qui transforme la panne du 2026-08-14 en un verdict `orphelin` au
   lieu d'un `en_cours` éternel.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
import redis
import redis.asyncio
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
    CLE_BATTEMENTS,
    SEUIL_ORPHELIN_S,
    CoeurRun,
    RegistreBattementsRedis,
    batteur_redis,
    horodatage_battement,
)
from maestro.controltower.events import EVENEMENT_EXECUTION_STATUT
from maestro.controltower.executions import ServiceExecutions
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_ATTENTE_REPONSES,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
)
from maestro.engine import STATUT_TERMINEE, RunReport, TaskResult
from maestro.engine import cli as engine_cli
from maestro.engine.loop import OrchestrationEngine

MAINTENANT = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)

#: Plafond d'attente d'un battement posé par une horloge (cœur du service, fil du
#: `CoeurRun`). Très au-dessus du nécessaire — les périodes utilisées ici sont de
#: quelques millisecondes —, jamais atteint quand tout va bien.
DELAI_ATTENTE_S = 5.0


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


# ------------------------------- ④ les deux hôtes battent au même endroit (#351)


class ClientRedisFactice:
    """Le strict nécessaire d'un client Redis : un hash, et rien d'autre.

    Il sert les **deux** clients à la fois — le synchrone de `batteur_redis` et
    l'asynchrone de `RegistreBattementsRedis` —, ce qui est le sujet : en production
    ce n'est pas la même bibliothèque mais c'est la même **instance**, et un hash
    écrit d'un côté doit se relire de l'autre.

    Il rend des **octets**, comme le vrai client par défaut : c'est ce qui exerce le
    décodage de la lecture, sans lequel les `run_id` ressortiraient sous forme de
    `b"..."` et ne s'appareilleraient avec aucun run de la projection.
    """

    def __init__(self) -> None:
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.ferme = False

    def hset(self, cle: str, champ: str, valeur: str) -> None:
        self.hashes.setdefault(cle, {})[champ.encode()] = valeur.encode()

    async def hgetall(self, cle: str) -> dict[bytes, bytes]:
        return dict(self.hashes.get(cle, {}))

    async def hdel(self, cle: str, champ: str) -> None:
        self.hashes.get(cle, {}).pop(champ.encode(), None)

    async def aclose(self) -> None:
        self.ferme = True


@pytest.fixture()
def redis_factice(monkeypatch) -> ClientRedisFactice:
    """Une seule instance Redis, servie aux deux fabriques de clients.

    `from_url` est remplacée sur les deux classes plutôt que sur un module :
    `batteur_redis` et `RegistreBattementsRedis` importent `redis` **localement**,
    donc résolvent l'attribut à l'appel — un module doublé au niveau de
    `sys.modules` ne serait pas vu.
    """
    faux = ClientRedisFactice()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: faux)
    monkeypatch.setattr(redis.asyncio.Redis, "from_url", lambda *_a, **_k: faux)
    return faux


def test_le_batteur_de_la_cli_ecrit_dans_le_hash_que_l_api_relit(redis_factice):
    """La clé partagée, seul endroit où les deux moitiés du dispositif se rejoignent.

    `maestro-run --publier` bat en **synchrone** (il n'a pas de boucle asyncio),
    l'API relit en **asynchrone** ; ce sont deux clients, deux fabriques et deux
    fichiers. Rien dans le code ne rapproche les deux moitiés sauf `CLE_BATTEMENTS`,
    et une divergence y serait invisible partout ailleurs : chaque moitié
    fonctionnerait parfaitement, et tout run publié hors de l'API ressortirait
    `indetermine` pour toujours — c'est-à-dire l'écran d'avant #348, en plus cher.
    """
    batteur_redis()("run-cli")

    assert set(redis_factice.hashes) == {CLE_BATTEMENTS}

    registre = RegistreBattementsRedis()
    battements = asyncio.run(registre.battements())
    assert set(battements) == {"run-cli"}
    # Décodé, donc appariable avec un `run_id` de la projection — et lisible par
    # `vitalite`, qui attend un horodatage ISO-8601 et non des octets.
    assert vitalite(EXECUTION_EN_COURS, battements["run-cli"]) == VITALITE_VIVANT

    asyncio.run(registre.oublier("run-cli"))
    assert asyncio.run(registre.battements()) == {}


def test_construire_les_clients_de_production_n_ouvre_aucune_connexion():
    """Les deux fabriques sont **paresseuses** — construites ici, ouvertes au premier appel.

    C'est ce qui permet à `maestro-run` de câbler son cœur sans exiger un Redis
    joignable, et à `create_default_app` de se construire avant que Redis ne soit
    debout. Le test le vérifie du seul endroit où ça se voit : ici, où aucun réseau
    n'est disponible (`tests/conftest.py`). Sans le doublon de la fixture, donc : ce
    sont les **vrais** clients qu'on construit.
    """
    assert callable(batteur_redis("redis://exemple.test:6379/0"))
    assert isinstance(
        RegistreBattementsRedis("redis://exemple.test:6379/0"), RegistreBattementsRedis
    )


class MoteurQuiRegardeLeCoeur:
    """Moteur factice qui note **combien de battements** ont déjà eu lieu à son entrée.

    C'est la seule façon d'éprouver « le cœur bat avant le premier appel modèle » :
    une fois le run fini, un battement posé avant et un battement posé après sont
    indiscernables dans le registre. Or la phase la plus lente d'un run est le
    cadrage, tout au début — la laisser sans signal de vie ferait passer orphelin le
    run qui en aurait justement le plus besoin.
    """

    def __init__(self, battus: list[str]) -> None:
        self._battus = battus
        self.avant_le_run: int | None = None

    async def run(self, objectif, *, journal=None, **_kwargs):
        self.avant_le_run = len(self._battus)
        return RunReport(
            objectif=objectif,
            resultats=(
                TaskResult(
                    task_id="t1",
                    titre="t1",
                    agent="developpeur",
                    role="Développeur",
                    competences_requises=(),
                    score=1,
                    statut=STATUT_TERMINEE,
                    sortie="Livrable",
                    erreur=None,
                ),
            ),
        )


@pytest.fixture()
def cli_sans_redis(monkeypatch) -> list[str]:
    """Monte `maestro-run` avec un batteur qui note, et sans publication d'événements.

    Deux doublures, chacune pour sa raison. Le **batteur** est remplacé pour que le
    cœur — le vrai `CoeurRun`, construit par le vrai `_battement_du_run` — batte
    dans une liste plutôt que vers Redis. La **publication des étapes**, elle, est
    neutralisée parce qu'elle pose un handler sur le logger **global**
    `maestro.trace` que rien ne retire : il survivrait au test et ferait partir
    chaque ligne journalisée ensuite vers un Redis absent (même famille de fuite que
    l'export Langfuse, `tests/conftest.py`). C'est aussi ce qui isole le sujet :
    `--publier` a deux effets, et seul le second est en cause ici.
    """
    battus: list[str] = []
    monkeypatch.setattr(
        "maestro.controltower.battement.batteur_redis", lambda *_a, **_k: battus.append
    )
    monkeypatch.setattr(engine_cli, "activer_publication_evenements", lambda: None)
    return battus


def test_publier_fait_battre_le_run_avant_le_premier_appel_modele(
    monkeypatch, capsys, cli_sans_redis
):
    """`maestro-run --publier` est un hôte : il bat tant qu'il vit, dès son départ.

    Sans ce câblage, le troisième critère de #348 n'existe qu'en théorie — la règle
    saurait reconnaître vivant un run publié hors de l'API, mais aucun run publié
    hors de l'API ne battrait. Et le battement doit précéder le run, pas le suivre :
    entre le lancement et le premier tour d'horloge, le run serait lu `indetermine`,
    donc indiscernable d'un run antérieur au dispositif.
    """
    moteur = MoteurQuiRegardeLeCoeur(cli_sans_redis)
    monkeypatch.setattr(
        OrchestrationEngine, "default", staticmethod(lambda **_: moteur)
    )

    assert engine_cli.main(["--publier", "Prototyper un mini-CRM"]) == 0
    capsys.readouterr()  # la synthèse Markdown, sans intérêt ici

    assert cli_sans_redis  # l'hôte a battu
    assert moteur.avant_le_run == 1  # …et il avait battu avant d'appeler le modèle
    # Un seul run, donc un seul identifiant : le cœur bat pour *son* run.
    assert len(set(cli_sans_redis)) == 1


def test_sans_publier_aucun_coeur_ne_part(monkeypatch, capsys, cli_sans_redis):
    """Le contre-test : le battement est adossé à `--publier`, jamais posé d'office.

    La question « l'API voit-elle ce run ? » a déjà une réponse, c'est cette
    option-là. Un run qui ne publie rien n'apparaît nulle part, donc il n'y a aucun
    `en_cours` à ne pas laisser traîner — et le faire battre quand même remplirait le
    registre d'entrées que personne ne consulte, chacune promise à ressortir
    `orphelin`.
    """
    monkeypatch.setattr(
        OrchestrationEngine,
        "default",
        staticmethod(lambda **_: MoteurQuiRegardeLeCoeur(cli_sans_redis)),
    )

    assert engine_cli.main(["Prototyper un mini-CRM"]) == 0
    capsys.readouterr()

    assert cli_sans_redis == []


def test_le_coeur_de_la_cli_s_arrete_sans_rien_effacer(monkeypatch, capsys):
    """La fin d'un run CLI **arrête** le cœur, elle n'efface pas son dernier battement.

    C'est ce qui distingue « l'hôte a fini » (le battement se tait, le run passera
    `orphelin`) de « ce run n'a jamais battu » (`indetermine`) — deux phrases
    différentes, et le corollaire assumé de #348 : un run `--publier` terminé
    normalement finit par apparaître orphelin, faute de publier un statut de fin. Le
    verdict porte sur son **hôte**, jamais sur son travail.
    """
    registre = RegistreBattementsMemoire()

    def battre(run_id: str) -> None:
        asyncio.run(registre.battre(run_id))

    monkeypatch.setattr(
        "maestro.controltower.battement.batteur_redis", lambda *_a, **_k: battre
    )
    monkeypatch.setattr(engine_cli, "activer_publication_evenements", lambda: None)
    monkeypatch.setattr(
        OrchestrationEngine,
        "default",
        staticmethod(lambda **_: MoteurQuiRegardeLeCoeur([])),
    )

    assert engine_cli.main(["--publier", "Objectif"]) == 0
    capsys.readouterr()

    (battement,) = asyncio.run(registre.battements()).values()
    plus_tard = datetime.now(UTC) + timedelta(seconds=SEUIL_ORPHELIN_S + 60)
    assert vitalite(EXECUTION_EN_COURS, battement, maintenant=plus_tard) == (
        VITALITE_ORPHELIN
    )


# ----------------- ⑤ ce que la mort de l'hôte laisse derrière elle (#351)


class RegistreCompteur(RegistreBattementsMemoire):
    """Registre mémoire qui **note chaque pose** — un battement rafraîchi se voit.

    Nécessaire parce que l'horodatage est à la **seconde** (`timespec="seconds"`,
    la forme des événements) : deux battements posés dans la même seconde portent la
    même valeur, si bien qu'un cœur arrêté et un cœur qui bat sont indiscernables
    dans le registre seul.
    """

    def __init__(self) -> None:
        super().__init__()
        self.poses: list[str] = []

    async def battre(self, run_id: str, *, horodatage: str | None = None) -> None:
        self.poses.append(run_id)
        await super().battre(run_id, horodatage=horodatage)


def _attendre(condition, quoi: str) -> None:
    """Attend qu'une horloge ait tourné — jamais plus que `DELAI_ATTENTE_S`."""
    limite = time.monotonic() + DELAI_ATTENTE_S
    while not condition():
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"{quoi} n'est jamais arrivé en {DELAI_ATTENTE_S} s")
        time.sleep(0.01)


def test_le_coeur_du_service_rafraichit_les_runs_en_vol():
    """Un run long doit continuer de battre : sinon il devient orphelin en travaillant.

    Le premier battement est posé par le lancement ; ce sont les **suivants** qui
    font la différence, un run pouvant durer des heures. Un seul cœur pour tous les
    runs du service, et non un par run : ce qu'il publie ne dépend que de la liste
    des runs en vol.
    """
    registre = RegistreCompteur()
    service = ServiceExecutions(
        InMemoryEventBus(),
        ControlTowerState(),
        fabrique_moteur=MoteurEnVol,
        battements=registre,
        periode_battement_s=0.01,
    )

    async def scenario() -> None:
        run_id = (await service.lancer("Prototyper"))["run_id"]
        assert registre.poses == [run_id]  # le lancement bat avant de rendre la main
        while registre.poses.count(run_id) < 3:
            await asyncio.sleep(0.01)
        await service.fermer()

    asyncio.run(asyncio.wait_for(scenario(), DELAI_ATTENTE_S))


def test_le_coeur_ne_rebat_pas_un_run_qui_a_deja_consigne_son_issue():
    """« En vol » se juge sur **deux** choses, et la seconde n'est pas du style.

    Une issue est consignée *avant* que la tâche ne s'éteigne — `annuler` le fait
    explicitement, `_derouler` en sortant, et un `brief.decision` refusé arrive même
    d'un autre process. S'en tenir à « la tâche n'est pas finie » laisserait donc le
    cœur reposer un battement juste après le retrait, et l'entrée d'un run soldé
    resterait dans le registre **pour toujours** — sur une clé qu'aucun TTL n'expire,
    à dessein.

    Le décor reproduit exactement cette fenêtre : la tâche de fond tourne encore (le
    moteur ne rend jamais la main), mais la projection porte déjà un statut terminal.
    """
    registre = RegistreCompteur()
    etat = ControlTowerState()
    service = ServiceExecutions(
        InMemoryEventBus(),
        etat,
        fabrique_moteur=MoteurEnVol,
        battements=registre,
        periode_battement_s=0.01,
    )

    async def scenario() -> None:
        run_id = (await service.lancer("Prototyper"))["run_id"]
        # L'issue arrive sans passer par le service — c'est le cas d'un run dont le
        # brief est refusé depuis un autre process. Sa tâche, elle, tourne encore.
        etat.appliquer(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=run_id,
                statut=EXECUTION_ANNULEE,
            )
        )
        assert service.en_vol(run_id)  # la tâche de fond n'est pas éteinte

        poses = len(registre.poses)
        await asyncio.sleep(0.1)  # une dizaine de périodes
        assert len(registre.poses) == poses

        await service.fermer()

    asyncio.run(asyncio.wait_for(scenario(), DELAI_ATTENTE_S))


def test_l_arret_de_l_api_n_efface_aucun_battement():
    """La panne du 2026-08-14, jouée jusqu'à son verdict.

    L'API s'arrête avec un run en vol — fenêtre du navigateur fermée, machine
    endormie. Le run meurt avec elle, et rien ne publie « je suis mort » : sa
    projection en reste à `en_cours`. Ce qui reste, et **doit** rester, c'est son
    dernier battement — il vieillit, et c'est lui qui rendra `orphelin` là où le
    journal durable rendait `en_cours` pour toujours.

    Effacer les battements à l'arrêt serait le geste de propreté qui reperd tout : le
    run redeviendrait `indetermine`, donc indiscernable des quatre fantômes du
    2026-08-17, et le dispositif ne dirait plus rien de la seule panne qui l'a
    motivé.
    """
    registre = RegistreBattementsMemoire()
    with TestClient(_app(registre)) as client:
        run_id = client.post("/api/executions", json={"objectif": "Prototyper"}).json()[
            "run_id"
        ]
        assert run_id in asyncio.run(registre.battements())
    # Sortie du `with` : lifespan refermé, service fermé, runs en vol annulés — ce
    # que fait l'arrêt de `maestro-api`, à ceci près qu'un `kill` ne le fait pas.

    battements = asyncio.run(registre.battements())
    assert run_id in battements

    plus_tard = datetime.now(UTC) + timedelta(seconds=SEUIL_ORPHELIN_S + 60)
    assert vitalite(EXECUTION_EN_COURS, battements[run_id], maintenant=plus_tard) == (
        VITALITE_ORPHELIN
    )
