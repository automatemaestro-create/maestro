"""Persistance locale : SQLite derrière la couche d'accès, sans service (#639).

Ce que le lot promet, et ce que cette suite éprouve dans le même ordre :

① **Le même contrat que Redis.** `SqliteEventLog` consigne et relit comme les
   deux autres implémentations, dans l'ordre de consignation, et ce qu'une
   instance a écrit, une **autre** le relit — c'est ce que fait un redémarrage
   de l'API, et c'est ce qui manquait au mode local : rien ne survivait.

② **La projection se reconstruit, sans qu'aucun service tourne.** Le rejeu du
   lifespan est celui d'avant, au code près : ce qui a été publié sur un
   `BusDurable` adossé au fichier resurgit tâche par tâche et coût par coût dans
   une app neuve montée sur ce même fichier.

③ **Le support se choisit par réglage.** `MAESTRO_PERSISTANCE` est lu à un seul
   endroit (`support_persistance`, `_supports_configures`) ; le fichier vit hors
   du dépôt et porte le nom de l'espace de la stack ; une valeur inconnue est une
   erreur franche.

④ **Sans réglage, rien ne bouge.** Les quatre supports restent les objets Redis
   d'avant ce lot, et l'hôte des runs reste le détaché.

Aucun test n'écrit sous `~/.maestro` : les journaux vivent sous `tmp_path`, et
seuls les tests du **chemin** regardent le défaut — qu'ils calculent sans le
créer.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.config import ConfigError, Settings
from maestro.controltower import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_TACHE_STATUT,
    BusDurable,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    RedisEventLog,
    RegistreBattementsMemoire,
    SqliteEventLog,
    chemin_sqlite,
    create_app,
    journal_configure,
    support_persistance,
)
from maestro.controltower.app import _hote_configure, _supports_configures
from maestro.controltower.battement import RegistreBattementsRedis
from maestro.controltower.donnees import annonce, donnees_de_la_stack
from maestro.controltower.events import RedisEventBus
from maestro.controltower.hote_detache import HoteRunDetache
from maestro.controltower.persistence import (
    ATTENTE_VERROU_MS,
    DOSSIER_LOCAL,
    SUPPORT_REDIS,
    SUPPORT_SQLITE,
)
from maestro.espace import racine_de_la_copie
from maestro.messaging import InMemoryMailbox, RedisMailbox
from maestro.telemetry import StepUsage

RUN = "run-local"


def reglages(**champs: object) -> Settings:
    """Un `Settings` réel dont seuls les champs nommés sont choisis.

    Recopié d'un `Settings` minimal plutôt qu'écrit en entier : ce qu'on éprouve
    est la **résolution d'un réglage**, et lister ici les champs de `Settings`
    ferait échouer ce fichier au prochain réglage ajouté ailleurs (même parti
    pris que `tests/test_hote_run.py`).
    """
    return dataclasses.replace(
        Settings(
            anthropic_api_key=None,
            anthropic_model="claude-opus-5",
            claude_auth_mode=None,
            claude_oauth_token=None,
            database_url=None,
            redis_url=None,
        ),
        **champs,  # type: ignore[arg-type] - les noms sont ceux de `Settings`
    )


def statut_tache(tache_id: str, *, titre: str, cout_usd: float) -> Event:
    """Un `tache.statut` terminé, avec sa mesure — la matière du grand livre."""
    return Event(
        type=EVENEMENT_TACHE_STATUT,
        run_id=RUN,
        tache_id=tache_id,
        titre=titre,
        agent="developpeur",
        role="Développeur",
        statut="terminee",
        cout_usd=cout_usd,
        usage=StepUsage(appels=1, tokens_entree=500, tokens_sortie=60, cout_usd=cout_usd),
    )


def consigner(journal: SqliteEventLog, *evenements: Event) -> None:
    """Consigne puis referme — ce que fait un process qui écrit puis s'arrête."""

    async def _faire() -> None:
        for event in evenements:
            await journal.consigner(event)
        await journal.close()

    asyncio.run(_faire())


def relire(journal: SqliteEventLog) -> list[Event]:
    """Relit puis referme — ce que fait un process qui démarre sur l'existant."""

    async def _faire() -> list[Event]:
        try:
            return await journal.relire()
        finally:
            await journal.close()

    return asyncio.run(_faire())


# ── ① Le même contrat que Redis ───────────────────────────────────────────────


def test_le_journal_rend_ce_qu_il_a_consigne_dans_l_ordre(tmp_path: Path) -> None:
    """`consigner`/`relire` : l'ordre de consignation est celui du rejeu."""
    fichier = tmp_path / "journal.sqlite3"
    consigner(
        SqliteEventLog(fichier),
        statut_tache("t1", titre="Schéma", cout_usd=0.4),
        statut_tache("t2", titre="API", cout_usd=0.2),
    )

    relus = relire(SqliteEventLog(fichier))

    assert [event.tache_id for event in relus] == ["t1", "t2"]
    assert [event.titre for event in relus] == ["Schéma", "API"]


def test_un_evenement_complet_fait_l_aller_retour_sans_rien_perdre(tmp_path: Path) -> None:
    """Le journal transporte le **JSON de l'événement**, exactement comme Redis.

    Même sérialisation (`Event.to_json`), donc même relecture : ce qui survit au
    redémarrage sur Redis survit au redémarrage sur le fichier, à l'octet près.
    Un champ qui ne ferait pas le voyage se verrait ici avant de se voir à
    l'écran.
    """
    fichier = tmp_path / "journal.sqlite3"
    original = statut_tache("t1", titre="Schéma & clés « uniques »", cout_usd=0.4)

    consigner(SqliteEventLog(fichier), original)
    (relu,) = relire(SqliteEventLog(fichier))

    assert relu == Event.from_json(original.to_json())
    assert relu.titre == original.titre  # accents et guillemets compris
    assert relu.usage is not None and relu.usage.tokens_entree == 500


def test_un_journal_qui_n_existe_pas_encore_se_relit_vide(tmp_path: Path) -> None:
    """Premier démarrage d'un poste : pas de fichier, pas d'histoire, **pas d'erreur**.

    Le fichier et son dossier sont créés au premier accès, et non à la
    construction : `create_default_app` fabrique l'app sans rien écrire sur le
    disque.
    """
    fichier = tmp_path / "jamais" / "ouvert" / "journal.sqlite3"
    journal = SqliteEventLog(fichier)

    assert not fichier.parent.exists(), "se construire n'écrit rien"
    assert relire(journal) == []
    assert fichier.exists()


def test_ce_qu_une_instance_consigne_une_autre_le_relit(tmp_path: Path) -> None:
    """La durabilité inter-redémarrage, dans sa forme la plus nue.

    Deux instances, deux connexions, un seul fichier : c'est exactement ce que
    voit l'API qu'on arrête et qu'on relance. `InMemoryEventLog` retrouverait ici
    une liste vide — c'est toute la différence que ce lot apporte au mode local.
    """
    fichier = tmp_path / "journal.sqlite3"
    consigner(SqliteEventLog(fichier), statut_tache("t1", titre="Schéma", cout_usd=0.4))

    consigner(SqliteEventLog(fichier), statut_tache("t2", titre="API", cout_usd=0.2))

    assert [event.tache_id for event in relire(SqliteEventLog(fichier))] == ["t1", "t2"]


def test_le_regime_de_concurrence_est_pose_sur_le_fichier(tmp_path: Path) -> None:
    """WAL et `busy_timeout` : le régime annoncé dans le ticket, vérifié sur la base.

    WAL est ce qui autorise **plusieurs process** sur le même fichier (l'API et
    un producteur voisin) sans qu'un lecteur bloque l'écrivain ; `busy_timeout`
    est ce qui fait **attendre** une écriture qui trouve le verrou pris, au lieu
    d'échouer sur-le-champ. Le mode WAL est écrit dans l'en-tête de la base :
    c'est donc bien le fichier qu'on interroge, et non l'objet qui l'a posé.
    """
    fichier = tmp_path / "journal.sqlite3"
    journal = SqliteEventLog(fichier)
    consigner(journal, statut_tache("t1", titre="Schéma", cout_usd=0.4))

    # Sur la connexion du journal : `busy_timeout` est propre à une connexion,
    # c'est donc là qu'il se lit.
    ouverte = SqliteEventLog(fichier)
    connexion = ouverte._connexion_ouverte()
    try:
        mode = connexion.execute("PRAGMA journal_mode").fetchone()[0]
        attente = connexion.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        asyncio.run(ouverte.close())

    assert str(mode).lower() == "wal"
    assert attente == ATTENTE_VERROU_MS

    # WAL, lui, est écrit dans l'en-tête de la base : une connexion qui n'a rien
    # réglé le retrouve, ce qui prouve que le mode vit sur le **fichier** — donc
    # qu'un second process l'ouvre déjà en WAL.
    temoin = sqlite3.connect(str(fichier))
    try:
        assert str(temoin.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
    finally:
        temoin.close()


def test_deux_ecrivains_sur_le_meme_fichier_n_en_perdent_aucun(tmp_path: Path) -> None:
    """Deux connexions qui appendent en même temps : rien ne se perd, rien ne lève.

    C'est le mécanisme qu'emprunte un second **process** (l'API et un producteur
    voisin) : chaque écriture prend le verrou d'écriture à son tour, et
    `busy_timeout` fait patienter celle qui arrive pendant. Le test les fait
    s'entrelacer dans une seule boucle asyncio, ce qui suffit à exercer le
    verrou : les connexions sont distinctes, et SQLite n'en sait pas plus.
    """
    fichier = tmp_path / "journal.sqlite3"
    api = SqliteEventLog(fichier)
    voisin = SqliteEventLog(fichier)

    async def _ecrire(journal: SqliteEventLog, prefixe: str) -> None:
        for index in range(20):
            await journal.consigner(statut_tache(f"{prefixe}{index}", titre="T", cout_usd=0.1))

    async def _faire() -> None:
        await asyncio.gather(_ecrire(api, "a"), _ecrire(voisin, "b"))
        await api.close()
        await voisin.close()

    asyncio.run(_faire())

    relus = [event.tache_id for event in relire(SqliteEventLog(fichier))]
    assert len(relus) == 40
    assert set(relus) == {f"a{i}" for i in range(20)} | {f"b{i}" for i in range(20)}


def test_le_passage_en_wal_attend_son_tour_au_lieu_d_echouer(tmp_path: Path) -> None:
    """Le perdant de la course à l'ouverture **repasse**, il ne lève pas.

    Ce que le test précédent n'attrape qu'une fois sur cinq : deux connexions
    qui ouvrent le même fichier neuf se disputent le verrou exclusif du passage
    en WAL, et SQLite rend là `SQLITE_BUSY` **sans** consulter le gestionnaire
    d'attente — poser `busy_timeout` avant n'y change rien. Joué ici sur une
    connexion doublée, donc sans dépendre d'un entrelacement : le premier essai
    échoue, le second trouve la base déjà en WAL.
    """

    class ConnexionQuiPerdLaCourse:
        """Une connexion dont le premier `journal_mode=WAL` trouve le verrou pris."""

        def __init__(self) -> None:
            self.instructions: list[str] = []
            self.essais_wal = 0

        def execute(self, instruction: str, *args: object) -> object:
            self.instructions.append(instruction)
            if not instruction.startswith("PRAGMA journal_mode=WAL"):
                return self
            self.essais_wal += 1
            if self.essais_wal == 1:
                raise sqlite3.OperationalError("database is locked")
            return self

        def fetchone(self) -> tuple[str]:
            return ("wal",)

    connexion = ConnexionQuiPerdLaCourse()
    SqliteEventLog(tmp_path / "journal.sqlite3")._passer_en_wal(connexion)  # type: ignore[arg-type]

    assert connexion.essais_wal == 2, "le perdant doit repasser, pas propager l'erreur"
    assert connexion.instructions == [
        "PRAGMA journal_mode=WAL",
        "PRAGMA journal_mode=WAL",
    ]


# ── ② La projection se reconstruit, sans service ──────────────────────────────


def test_le_grand_livre_survit_au_redemarrage_sans_aucun_service(tmp_path: Path) -> None:
    """Le critère du ticket, joué de bout en bout : publier, arrêter, relancer.

    La « session précédente » publie sur un `BusDurable` adossé au fichier —
    c'est le bus que `create_app` monte, et c'est lui qui **consigne en
    publiant** (#699). L'API relancée ne partage avec elle qu'un fichier : ni
    Redis, ni mémoire commune. Elle rejoue, et le grand livre du run réapparaît à
    l'identique.
    """
    fichier = tmp_path / "journal.sqlite3"

    async def _session_precedente() -> None:
        bus = BusDurable(InMemoryEventBus(), SqliteEventLog(fichier), possede_le_journal=True)
        await bus.publish(
            Event(
                type=EVENEMENT_AGENT_ACTIVITE,
                run_id=RUN,
                agent="orchestrateur",
                role="Orchestrateur",
                statut="terminee",
                usage=StepUsage(appels=1, tokens_entree=200, tokens_sortie=20, cout_usd=0.1),
            )
        )
        await bus.publish(statut_tache("t1", titre="Schéma", cout_usd=0.4))
        await bus.publish(statut_tache("t2", titre="API", cout_usd=0.2))
        await bus.close()

    asyncio.run(_session_precedente())

    # Redémarrage : app neuve, projection vide, MÊME fichier → rejeu au démarrage.
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), event_log=SqliteEventLog(fichier)
    )
    with TestClient(app) as redemarree:
        taches = {t["id"] for t in redemarree.get("/api/taches?projet=tous").json()}
        cout = redemarree.get(f"/api/executions/{RUN}/cout").json()

    assert taches == {"t1", "t2"}
    assert cout["planification"]["cout_usd"] == pytest.approx(0.1)
    assert cout["total"]["cout_usd"] == pytest.approx(0.7)


def test_le_journal_requetable_se_remplit_du_meme_rejeu(tmp_path: Path) -> None:
    """Le journal requêtable (#478) est une **vue** du durable, quel qu'en soit le support.

    Rien de particulier n'a été écrit pour lui : il se remplit du parcours du
    rejeu, donc du fichier, exactement comme il se remplissait de la liste Redis.
    """
    fichier = tmp_path / "journal.sqlite3"
    consigner(
        SqliteEventLog(fichier),
        statut_tache("t1", titre="Schéma", cout_usd=0.4),
        statut_tache("t2", titre="API", cout_usd=0.2),
    )

    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), event_log=SqliteEventLog(fichier)
    )
    with TestClient(app) as redemarree:
        entrees = redemarree.get("/api/journal?projet=tous").json()["entrees"]

    assert {entree["tache_id"] for entree in entrees} == {"t1", "t2"}


# ── ③ Le support se choisit par réglage ───────────────────────────────────────


@pytest.mark.parametrize("valeur", [None, "", SUPPORT_REDIS, " REDIS "])
def test_sans_reglage_le_support_est_redis(valeur: str | None) -> None:
    """Le silence désigne le chemin de production — le comportement d'avant ce lot."""
    assert support_persistance(reglages(persistance=valeur)) == SUPPORT_REDIS


@pytest.mark.parametrize("valeur", [SUPPORT_SQLITE, " SQLITE "])
def test_le_mode_local_se_nomme(valeur: str) -> None:
    assert support_persistance(reglages(persistance=valeur)) == SUPPORT_SQLITE


def test_un_support_inconnu_est_une_erreur_franche_qui_nomme_les_deux() -> None:
    """`MAESTRO_PERSISTANCE=sqlit` ne doit jamais ressembler à un choix.

    Un repli silencieux sur Redis laisserait croire qu'un poste persiste dans son
    fichier alors qu'il parle à un service absent — et cela ne se verrait qu'au
    premier redémarrage, c'est-à-dire une fois l'histoire perdue. Même parti pris
    que `MAESTRO_HOTE_RUN` (#446) et `MAESTRO_ISOLATION` (#108).
    """
    with pytest.raises(ConfigError) as refus:
        support_persistance(reglages(persistance="sqlit"))

    message = str(refus.value)
    assert "sqlit" in message
    assert SUPPORT_REDIS in message and SUPPORT_SQLITE in message


def test_la_fabrique_du_journal_suit_le_reglage(tmp_path: Path) -> None:
    """Un seul point de bascule : aucun appelant d'`EventLog` ne change."""
    local = journal_configure(
        reglages(persistance=SUPPORT_SQLITE, sqlite_fichier=str(tmp_path / "j.sqlite3"))
    )
    serveur = journal_configure(reglages())

    assert isinstance(local, SqliteEventLog)
    assert isinstance(serveur, RedisEventLog)


def test_le_fichier_par_defaut_vit_hors_du_depot_et_porte_l_espace() -> None:
    """Le critère « hors du dépôt », et la séparation des copies (#1164).

    Une installation n'a pas de clone, et un fichier rangé dans l'arbre de
    travail serait emporté par le premier ménage. Le nom dérive de l'espace :
    deux copies — et l'état du banc, `<espace>.banc` — ont chacune le leur sans
    qu'on ait rien à régler. Calculé, jamais créé : ce test n'écrit rien.
    """
    chemin = chemin_sqlite(reglages(persistance=SUPPORT_SQLITE))

    assert not chemin.is_relative_to(racine_de_la_copie())
    assert chemin.parent == Path.home() / DOSSIER_LOCAL
    assert chemin.name.startswith("commun")  # l'espace figé de la suite
    ailleurs = chemin_sqlite(reglages(), environnement={"MAESTRO_ESPACE": "1164-copie"})
    assert ailleurs.name.startswith("1164-copie")


def test_un_fichier_nomme_a_la_main_l_emporte(tmp_path: Path) -> None:
    """Un chemin réglé est un choix explicite — et `~` y est développé."""
    choisi = tmp_path / "ailleurs" / "journal.sqlite3"

    assert chemin_sqlite(reglages(sqlite_fichier=str(choisi))) == choisi
    assert chemin_sqlite(reglages(sqlite_fichier="~/journal.sqlite3")) == (
        Path.home() / "journal.sqlite3"
    )


def test_en_mode_local_les_quatre_supports_sont_mono_process(tmp_path: Path) -> None:
    """Ce que `MAESTRO_PERSISTANCE=sqlite` câble : une API, et rien à installer.

    Les quatre vont ensemble, et c'est le sujet : un journal local derrière un
    bus Redis exigerait encore un service, et un bus mémoire devant un journal
    Redis consignerait dans une instance qu'aucun autre process ne lit.
    """
    supports = _supports_configures(
        reglages(persistance=SUPPORT_SQLITE, sqlite_fichier=str(tmp_path / "j.sqlite3"))
    )

    assert isinstance(supports.bus, InMemoryEventBus)
    assert isinstance(supports.mailbox, InMemoryMailbox)
    assert isinstance(supports.journal, SqliteEventLog)
    assert isinstance(supports.battements, RegistreBattementsMemoire)


def test_en_mode_local_l_hote_des_runs_est_celui_de_l_api() -> None:
    """Le mode de distribution change les **défauts**, pas les fonctions (docs/24 §4.7).

    L'hôte détaché exige un Redis joignable : sans service, le défaut devient la
    tâche de fond de l'API. `None` est rendu pour elle, comme partout ailleurs —
    le service se donne alors son `HoteRunEnProcess` autour de son dérouleur.
    """
    assert _hote_configure(reglages(persistance=SUPPORT_SQLITE)) is None


def test_en_mode_local_l_hote_detache_demande_explicitement_est_refuse() -> None:
    """Un run qui partirait pour ne rien rapporter : mieux vaut un refus au démarrage.

    L'hôte détaché publie, bat et consigne **sur Redis**. Demandé dans un mode
    qui n'en a pas, il ferait un run dont rien ne reviendrait — muet à l'écran,
    absent du journal. Le message nomme les deux réglages en cause et la sortie.
    """
    with pytest.raises(ConfigError) as refus:
        _hote_configure(reglages(persistance=SUPPORT_SQLITE, hote_run="detache"))

    message = str(refus.value)
    assert "MAESTRO_HOTE_RUN" in message and "MAESTRO_PERSISTANCE" in message


def test_le_preflight_du_lanceur_ne_cherche_aucun_service_en_mode_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`start.sh` demande, Python résout — et refuserait un démarrage qui marche.

    Le préflight du lanceur (`maestro-api --verifier-redis`) rend `0` sans
    joindre personne, nomme le fichier du journal et annonce les mêmes données.
    Rien n'a été recopié en shell : le lanceur ne connaît aucun nom de variable.
    """
    from maestro.controltower import cli

    fichier = tmp_path / "journal.sqlite3"
    monkeypatch.setenv("MAESTRO_PERSISTANCE", SUPPORT_SQLITE)
    monkeypatch.setenv("MAESTRO_SQLITE_FICHIER", str(fichier))

    assert cli.verifier_redis() == 0

    sortie = capsys.readouterr().out
    assert "aucun service externe" in sortie
    assert str(fichier) in sortie
    assert "Redis joignable" not in sortie


def test_l_annonce_dit_le_fichier_plutot_que_des_cles_redis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """L'annonce existe pour dire **où vivent les données** : elle ne peut pas dire faux."""
    fichier = tmp_path / "journal.sqlite3"
    monkeypatch.setenv("MAESTRO_PERSISTANCE", SUPPORT_SQLITE)
    monkeypatch.setenv("MAESTRO_SQLITE_FICHIER", str(fichier))

    ligne = next(ligne for ligne in annonce(donnees_de_la_stack()) if ligne.split()[0] == "runs")

    assert str(fichier) in ligne
    assert "Redis" not in ligne


# ── ④ Sans réglage, rien ne bouge ─────────────────────────────────────────────


def test_sans_reglage_les_quatre_supports_restent_ceux_de_redis() -> None:
    """Le troisième critère du ticket : rien de ce qui marche aujourd'hui ne régresse.

    Les objets Redis se **construisent** sans que Redis réponde (connexion
    paresseuse) : c'est ce qui permet de le vérifier ici sans service.
    """
    supports = _supports_configures(reglages())

    assert isinstance(supports.bus, RedisEventBus)
    assert isinstance(supports.mailbox, RedisMailbox)
    assert isinstance(supports.journal, RedisEventLog)
    assert isinstance(supports.battements, RegistreBattementsRedis)


def test_sans_reglage_l_hote_des_runs_reste_le_detache() -> None:
    """Le défaut de #446 n'a pas bougé d'un cran — seul le mode local le déplace."""
    assert isinstance(_hote_configure(reglages()), HoteRunDetache)


def test_sans_reglage_l_annonce_parle_toujours_de_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAESTRO_PERSISTANCE", raising=False)

    ligne = next(ligne for ligne in annonce(donnees_de_la_stack()) if ligne.split()[0] == "runs")

    assert "Redis" in ligne and "sans préfixe" in ligne  # l'espace commun, figé par la suite
