"""Le schéma du journal local se met à niveau sans casser une installation (#1621).

Le fichier SQLite du mode local (`maestro.controltower.persistence`) porte le journal
des événements et l'état acquis des runs. Ce que le lot promet, éprouvé dans l'ordre
de ses critères, **toujours sur de vrais fichiers** :

① **La version est lue, puis le fichier est mis à niveau** par des marches ordonnées,
   chacune une seule fois — à deux écrivains aussi —, après une sauvegarde prise par
   l'API de SQLite. Une installation d'avant ce lot (v1, écrite par l'ancien code)
   est reprise sans rien perdre.

② **Une migration qui échoue remet le fichier tel qu'il était** : même schéma, mêmes
   lignes, même version, et la version précédente de Maestro le rouvre sans perte.
   Les migrations qui échouent ici échouent **réellement** : sur une contrainte que
   les données du fichier violent, et sur une marche qui valide en route — le cas que
   l'annulation de la transaction ne couvre pas et que seule la sauvegarde rattrape.
   Le démarrage dit la cause et ce qu'il reste à faire.

③ **Un fichier plus récent que le code n'est ni réécrit ni rétrogradé** : ses octets
   ne bougent pas, et le démarrage dit de mettre Maestro à jour.

Aucun test n'écrit sous `~/.maestro` : chaque fichier vit sous `tmp_path`.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    EVENEMENT_TACHE_STATUT,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    SqliteEventLog,
    create_app,
)
from maestro.controltower.acquis import MagasinAcquisSqlite
from maestro.controltower.persistence import (
    MIGRATIONS,
    SCHEMA_VERSION,
    SUFFIXE_SAUVEGARDE,
    TABLE_ACQUIS,
    TABLE_EVENEMENTS,
    Migration,
    MigrationEchouee,
    SchemaPlusRecent,
    chemin_sauvegarde,
    ouvrir_sqlite,
)
from maestro.orchestrator.schema import Task

RUN = "run-schema"


# ── Le décor : de vrais fichiers, et de vraies migrations ─────────────────────


def evenement(tache_id: str) -> Event:
    return Event(type=EVENEMENT_TACHE_STATUT, run_id=RUN, tache_id=tache_id, statut="terminee")


def tache(identifiant: str) -> Task:
    return Task(
        id=identifiant,
        title=f"Tâche {identifiant}",
        description=f"Réaliser {identifiant}.",
        required_competencies=("backend",),
        output_format="Texte",
    )


def fichier_d_avant_ce_lot(fichier: Path, *taches: str, avec_acquis: bool = False) -> None:
    """Un fichier tel que l'écrivait le code d'avant ce lot : v1, le journal seul.

    Recopié de ce que faisait `SqliteEventLog._ouvrir` — WAL, la table du journal,
    `user_version=1` posé sans condition — et, quand l'état acquis avait servi, sa
    table créée par lui-même sans que la version bouge. C'est le fichier qu'une
    installation existante porte sous `~/.maestro/`.
    """
    connexion = sqlite3.connect(str(fichier), isolation_level=None)
    try:
        connexion.execute("PRAGMA journal_mode=WAL")
        connexion.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE_EVENEMENTS} "
            "(rang INTEGER PRIMARY KEY, charge TEXT NOT NULL)"
        )
        for tache_id in taches:
            connexion.execute(
                f"INSERT INTO {TABLE_EVENEMENTS} (charge) VALUES (?)",
                (evenement(tache_id).to_json(),),
            )
        if avec_acquis:
            connexion.execute(
                f"CREATE TABLE IF NOT EXISTS {TABLE_ACQUIS} "
                "(run_id TEXT NOT NULL, cle TEXT NOT NULL, charge TEXT NOT NULL, "
                "PRIMARY KEY (run_id, cle))"
            )
            connexion.execute(
                f"INSERT INTO {TABLE_ACQUIS} VALUES (?, ?, ?)", (RUN, "plan", "[]")
            )
        connexion.execute("PRAGMA user_version=1")
    finally:
        connexion.close()


def fichier_a_jour(fichier: Path, *taches: str) -> None:
    """Un fichier au schéma du code, écrit par le code : le journal et l'état acquis d'un run."""
    consigner(SqliteEventLog(fichier), *(evenement(t) for t in taches))
    magasin = MagasinAcquisSqlite(fichier)

    async def _poser() -> None:
        await magasin.poser_plan(RUN, [tache("socle"), tache("pages")])
        await magasin.close()

    asyncio.run(_poser())


def consigner(journal: SqliteEventLog, *evenements: Event) -> None:
    async def _faire() -> None:
        for event in evenements:
            await journal.consigner(event)
        await journal.close()

    asyncio.run(_faire())


def relire(journal: SqliteEventLog) -> list[Event]:
    async def _faire() -> list[Event]:
        try:
            return await journal.relire()
        finally:
            await journal.close()

    return asyncio.run(_faire())


def plan_acquis(fichier: Path) -> tuple[str, ...]:
    magasin = MagasinAcquisSqlite(fichier)

    async def _lire() -> tuple[str, ...]:
        try:
            etat = await magasin.lire(RUN)
        finally:
            await magasin.close()
        return tuple(t.id for t in etat.plan) if etat is not None else ()

    return asyncio.run(_lire())


Releve = tuple[int, list[tuple[str, str, str | None]], list[tuple[object, ...]]]


def releve(fichier: Path) -> Releve:
    """Ce que porte le fichier, lu par une connexion témoin : version, schéma, lignes.

    Toutes les tables sont relues, pas seulement les deux du code : une migration
    restée à moitié passée laisserait une table ou une colonne en trop, et c'est
    précisément ce que cette lecture doit voir.
    """
    temoin = sqlite3.connect(str(fichier))
    try:
        version = int(temoin.execute("PRAGMA user_version").fetchone()[0])
        schema = sorted(
            (str(type_), str(nom), sql)
            for type_, nom, sql in temoin.execute("SELECT type, name, sql FROM sqlite_master")
        )
        lignes: list[tuple[object, ...]] = []
        for _type, nom, _sql in schema:
            if _type == "table":
                lignes.extend((nom, *ligne) for ligne in temoin.execute(f"SELECT * FROM {nom}"))
        return version, schema, sorted(lignes, key=repr)
    finally:
        temoin.close()


def version_de(fichier: Path) -> int:
    return releve(fichier)[0]


def marche_qui_compte(version: int) -> Migration:
    """Une marche qui consigne **dans le fichier** chacun de ses passages."""

    def appliquer(connexion: sqlite3.Connection) -> None:
        connexion.execute("CREATE TABLE IF NOT EXISTS passages (version INTEGER)")
        connexion.execute("INSERT INTO passages VALUES (?)", (version,))

    return Migration(version, "compteur de passages", appliquer)


def passages(fichier: Path) -> int:
    temoin = sqlite3.connect(str(fichier))
    try:
        return int(temoin.execute("SELECT count(*) FROM passages").fetchone()[0])
    finally:
        temoin.close()


def marche_contrainte(version: int, tentatives: list[int] | None = None) -> Migration:
    """Une marche qui échoue sur les **données** du fichier : une unicité qu'elles violent.

    Elle ajoute d'abord une colonne — un effet réel, qu'un échec ne doit pas laisser
    derrière lui —, puis pose un index unique sur la charge des événements : deux
    événements identiques le font échouer, comme un vrai journal peut en porter.
    """

    def appliquer(connexion: sqlite3.Connection) -> None:
        if tentatives is not None:
            tentatives.append(version)
        connexion.execute(f"ALTER TABLE {TABLE_EVENEMENTS} ADD COLUMN projet TEXT")
        connexion.execute(
            f"CREATE UNIQUE INDEX evenements_uniques ON {TABLE_EVENEMENTS} (charge)"
        )

    return Migration(version, "un événement par charge", appliquer)


def marche_qui_valide_en_route(version: int) -> Migration:
    """Une marche écrite avec `executescript`, qui **valide** la transaction avant d'échouer.

    `executescript` commence par un `COMMIT` : la mise à niveau perd sa transaction,
    la table créée ensuite est écrite pour de bon, puis l'instruction suivante échoue.
    L'annulation n'a plus rien à défaire — seule la sauvegarde remet le fichier.
    """

    def appliquer(connexion: sqlite3.Connection) -> None:
        connexion.executescript(
            "CREATE TABLE brouillon (x); INSERT INTO brouillon SELECT * FROM table_absente;"
        )

    return Migration(version, "brouillon", appliquer)


# ── ① La version est lue, puis le fichier est mis à niveau ────────────────────


def test_le_schema_du_code_monte_de_un_en_un() -> None:
    """La n-ième marche porte le fichier à la version n : pas de trou, pas de doublon."""
    assert [marche.version for marche in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))
    assert SCHEMA_VERSION == MIGRATIONS[-1].version


@pytest.mark.parametrize("versions", [(1, 3), (2, 1), (1, 1)])
def test_des_marches_hors_d_ordre_sont_refusees_avant_d_ouvrir(
    tmp_path: Path, versions: tuple[int, ...]
) -> None:
    fichier = tmp_path / "journal.sqlite3"
    marches = [marche_qui_compte(v) for v in versions]

    with pytest.raises(ValueError, match="hors d'ordre"):
        ouvrir_sqlite(fichier, migrations=marches)

    assert not fichier.exists(), "rien n'est créé pour des marches qu'on refuse"


def test_un_fichier_neuf_recoit_tout_le_schema_sans_sauvegarde(tmp_path: Path) -> None:
    """Premier démarrage : le schéma entier, et aucune sauvegarde d'un fichier qui ne porte rien."""
    fichier = tmp_path / "journal.sqlite3"

    assert relire(SqliteEventLog(fichier)) == []

    version, schema, _ = releve(fichier)
    assert version == SCHEMA_VERSION
    assert {nom for type_, nom, _ in schema if type_ == "table"} == {TABLE_EVENEMENTS, TABLE_ACQUIS}
    assert not chemin_sauvegarde(fichier).exists()


def test_une_installation_d_avant_ce_lot_est_reprise_apres_sauvegarde(tmp_path: Path) -> None:
    """Le fichier qu'une installation porte aujourd'hui : v1, le journal seul.

    Il passe au schéma du code sans perdre un événement, et l'état acquis y trouve sa
    table. La sauvegarde est le fichier **d'avant** : v1, les mêmes événements, et un
    fichier qui se suffit à lui-même (pas de `-wal` à côté d'elle).
    """
    fichier = tmp_path / "journal.sqlite3"
    fichier_d_avant_ce_lot(fichier, "t1", "t2")

    assert [event.tache_id for event in relire(SqliteEventLog(fichier))] == ["t1", "t2"]

    assert version_de(fichier) == SCHEMA_VERSION
    sauvegarde = chemin_sauvegarde(fichier)
    assert sauvegarde.name == f"journal.sqlite3{SUFFIXE_SAUVEGARDE}"
    version_sauvee, schema_sauve, lignes_sauvees = releve(sauvegarde)
    assert version_sauvee == 1
    assert {nom for _, nom, _ in schema_sauve} == {TABLE_EVENEMENTS}
    assert [ligne[0] for ligne in lignes_sauvees] == [TABLE_EVENEMENTS] * 2
    temoin = sqlite3.connect(str(sauvegarde))
    try:
        assert str(temoin.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "delete"
    finally:
        temoin.close()
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(sauvegarde.name)) == [
        sauvegarde.name
    ]
    assert plan_acquis(fichier) == ()  # la table existe, aucun run n'y a rien rangé


def test_l_etat_acquis_qui_ouvre_le_premier_met_le_fichier_a_niveau_pour_les_deux(
    tmp_path: Path,
) -> None:
    """Deux écrivains, un seul auteur du schéma : peu importe lequel ouvre le premier.

    L'état acquis ouvre une installation d'avant ce lot qui portait déjà sa table (il
    la créait lui-même) : la marche passe sans heurt, ses lignes restent, et le
    journal qui ouvre ensuite n'a plus rien à migrer — la sauvegarde n'est pas reprise.
    """
    fichier = tmp_path / "journal.sqlite3"
    fichier_d_avant_ce_lot(fichier, "t1", avec_acquis=True)
    magasin = MagasinAcquisSqlite(fichier)

    async def _poser() -> None:
        await magasin.poser_plan("run-2", [tache("socle")])
        await magasin.close()

    asyncio.run(_poser())
    assert version_de(fichier) == SCHEMA_VERSION
    sauvee = chemin_sauvegarde(fichier).stat().st_mtime_ns

    assert [event.tache_id for event in relire(SqliteEventLog(fichier))] == ["t1"]
    assert chemin_sauvegarde(fichier).stat().st_mtime_ns == sauvee
    _, _, lignes = releve(fichier)
    assert (TABLE_ACQUIS, RUN, "plan", "[]") in lignes, "l'état acquis d'avant est resté"


def test_chaque_marche_ne_se_joue_qu_une_fois(tmp_path: Path) -> None:
    """Rouvrir un fichier à jour ne rejoue rien : la version dit ce qui est passé."""
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1")
    marches = (*MIGRATIONS, marche_qui_compte(SCHEMA_VERSION + 1))

    for _ in range(3):
        ouvrir_sqlite(fichier, migrations=marches).close()

    assert passages(fichier) == 1
    assert version_de(fichier) == SCHEMA_VERSION + 1


def test_deux_ouvertures_simultanees_ne_migrent_qu_une_fois(tmp_path: Path) -> None:
    """L'API et un écrivain voisin qui trouvent ensemble le même fichier en retard.

    Une barrière les lâche en même temps (#292 : jamais un `sleep`). Le verrou
    d'écriture pris avant de relire la version fait passer la marche une fois : le
    perdant attend son tour sous `busy_timeout`, puis relit un fichier déjà à jour.
    """
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1")
    marches = (*MIGRATIONS, marche_qui_compte(SCHEMA_VERSION + 1))
    ouvreurs = 6
    depart = threading.Barrier(ouvreurs)
    erreurs: list[BaseException] = []

    def ouvrir() -> None:
        depart.wait()
        try:
            ouvrir_sqlite(fichier, migrations=marches).close()
        except BaseException as erreur:  # noqa: BLE001 - rapporté par l'assertion
            erreurs.append(erreur)

    fils = [threading.Thread(target=ouvrir) for _ in range(ouvreurs)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(timeout=60)

    assert erreurs == []
    assert passages(fichier) == 1
    assert version_de(fichier) == SCHEMA_VERSION + 1


def test_la_sauvegarde_est_prise_avant_que_la_marche_ne_commence(tmp_path: Path) -> None:
    """Au moment où la marche s'applique, la sauvegarde existe déjà et porte le fichier d'avant."""
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1", "t2")
    vu: list[tuple[int, int]] = []

    def appliquer(connexion: sqlite3.Connection) -> None:
        version, _, lignes = releve(chemin_sauvegarde(fichier))
        vu.append((version, sum(1 for ligne in lignes if ligne[0] == TABLE_EVENEMENTS)))
        connexion.execute(f"ALTER TABLE {TABLE_EVENEMENTS} ADD COLUMN projet TEXT")

    ouvrir_sqlite(
        fichier, migrations=(*MIGRATIONS, Migration(SCHEMA_VERSION + 1, "projet", appliquer))
    ).close()

    assert vu == [(SCHEMA_VERSION, 2)]


# ── ② Une migration qui échoue remet le fichier tel qu'il était ───────────────


def test_une_migration_qui_echoue_remet_le_fichier_tel_qu_il_etait(tmp_path: Path) -> None:
    """La marche ajoute une colonne, puis bute sur les données : rien d'elle ne reste."""
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1", "t1", "t2")  # deux événements identiques
    avant = releve(fichier)

    with pytest.raises(MigrationEchouee) as refus:
        relire(
            SqliteEventLog(
                fichier, migrations=(*MIGRATIONS, marche_contrainte(SCHEMA_VERSION + 1))
            )
        )

    assert releve(fichier) == avant
    echec = refus.value
    assert echec.restauree
    assert (echec.depuis, echec.vers) == (SCHEMA_VERSION, SCHEMA_VERSION + 1)
    assert "UNIQUE constraint failed" in echec.motif, "la cause est celle de SQLite"
    assert f"la migration v{SCHEMA_VERSION + 1} (un événement par charge)" in echec.motif


def test_une_marche_qui_valide_en_route_est_rattrapee_par_la_sauvegarde(tmp_path: Path) -> None:
    """Le cas que l'annulation ne couvre pas : la marche a écrit pour de bon avant d'échouer."""
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1", "t2")
    avant = releve(fichier)

    with pytest.raises(MigrationEchouee) as refus:
        ouvrir_sqlite(fichier, migrations=(*MIGRATIONS, marche_qui_valide_en_route(3)))

    assert releve(fichier) == avant, "ni la table du brouillon, ni la version 3 ne restent"
    assert refus.value.restauree
    assert "table_absente" in refus.value.motif


def test_une_marche_qui_valide_sans_echouer_est_une_marche_en_echec(tmp_path: Path) -> None:
    """Valider en route, c'est rendre ses effets inannulables : refusé, et le fichier remis."""
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1")
    avant = releve(fichier)

    def appliquer(connexion: sqlite3.Connection) -> None:
        connexion.executescript("CREATE TABLE brouillon (x);")

    with pytest.raises(MigrationEchouee) as refus:
        ouvrir_sqlite(
            fichier, migrations=(*MIGRATIONS, Migration(SCHEMA_VERSION + 1, "brouillon", appliquer))
        )

    assert releve(fichier) == avant
    assert "validé la transaction" in refus.value.motif


def test_la_version_precedente_rouvre_le_fichier_sans_perte(tmp_path: Path) -> None:
    """Après l'échec de la version suivante, le code d'aujourd'hui relit tout, et continue.

    La « version suivante » est ce code et une marche de plus ; la « version
    précédente », ce code tel quel. Elle relit chaque événement et l'état acquis du
    run, sans rien migrer, puis consigne à la suite.
    """
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1", "t1", "t2")
    with pytest.raises(MigrationEchouee):
        relire(
            SqliteEventLog(
                fichier, migrations=(*MIGRATIONS, marche_contrainte(SCHEMA_VERSION + 1))
            )
        )

    assert [e.tache_id for e in relire(SqliteEventLog(fichier))] == ["t1", "t1", "t2"]
    assert plan_acquis(fichier) == ("socle", "pages")
    consigner(SqliteEventLog(fichier), evenement("t3"))
    assert [e.tache_id for e in relire(SqliteEventLog(fichier))] == ["t1", "t1", "t2", "t3"]
    assert version_de(fichier) == SCHEMA_VERSION


def test_un_fichier_neuf_dont_la_migration_echoue_est_remis_vide(tmp_path: Path) -> None:
    """Sans sauvegarde à reprendre, le fichier est remis à ce qu'il était : vide."""
    fichier = tmp_path / "journal.sqlite3"

    with pytest.raises(MigrationEchouee) as refus:
        ouvrir_sqlite(fichier, migrations=(*MIGRATIONS, marche_qui_valide_en_route(3)))

    assert releve(fichier) == (0, [], [])
    assert refus.value.restauree
    assert refus.value.sauvegarde is None
    assert not chemin_sauvegarde(fichier).exists()
    assert relire(SqliteEventLog(fichier)) == [], "le code d'aujourd'hui l'ouvre ensuite"


def test_un_refus_est_retenu_le_fichier_n_est_pas_rejoue_a_chaque_essai(tmp_path: Path) -> None:
    """La pompe de l'API retente le rejeu : le fichier ne revit pas sauvegarde et restauration."""
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1", "t1")
    tentatives: list[int] = []
    journal = SqliteEventLog(
        fichier, migrations=(*MIGRATIONS, marche_contrainte(SCHEMA_VERSION + 1, tentatives))
    )

    async def _deux_essais() -> tuple[BaseException, BaseException]:
        refus = []
        for _ in range(2):
            try:
                await journal.relire()
            except MigrationEchouee as erreur:
                refus.append(erreur)
        await journal.close()
        return refus[0], refus[1]

    premier, second = asyncio.run(_deux_essais())

    assert tentatives == [SCHEMA_VERSION + 1]
    assert second is premier


def test_le_demarrage_dit_la_cause_et_ce_qu_il_reste_possible_de_faire(tmp_path: Path) -> None:
    """L'API démarre sur l'échec, le dit dans sa santé, et ne sert rien d'autre que la panne."""
    fichier = tmp_path / "journal.sqlite3"
    fichier_a_jour(fichier, "t1", "t1")
    avant = releve(fichier)
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        event_log=SqliteEventLog(
            fichier, migrations=(*MIGRATIONS, marche_contrainte(SCHEMA_VERSION + 1))
        ),
    )

    with TestClient(app) as client:
        sante = client.get("/api/sante").json()
        taches = client.get("/api/taches?projet=tous")

    magasin = sante["magasin"]
    assert sante["statut"] == "degrade"
    assert magasin["disponible"] is False
    assert magasin["titre"] == "Mise à niveau du journal local impossible"
    assert "UNIQUE constraint failed" in magasin["motif"]
    assert f"remis tel qu'il était, au schéma v{SCHEMA_VERSION}" in magasin["motif"]
    assert "la version précédente de Maestro rouvre ce fichier sans perte" in magasin["geste"]
    assert magasin["lieu"] == f"SQLite, {fichier}"
    assert taches.status_code == 503
    assert taches.json()["panne"] == "magasin"
    assert taches.json()["detail"].startswith("Mise à niveau du journal local impossible")
    assert releve(fichier) == avant


# ── ③ Un fichier plus récent que le code n'est ni réécrit ni rétrogradé ───────


def fichier_plus_recent(fichier: Path, *, mode: str) -> None:
    """Un fichier qu'une version plus récente de Maestro aurait écrit : v{code + 1}."""
    fichier_a_jour(fichier, "t1", "t2")
    connexion = sqlite3.connect(str(fichier), isolation_level=None)
    try:
        connexion.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        connexion.execute(f"PRAGMA journal_mode={mode}")
    finally:
        connexion.close()


def empreinte(dossier: Path) -> dict[str, tuple[bytes, int]]:
    """Chaque fichier du dossier, octet par octet, avec sa date : de quoi voir le moindre geste."""
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(dossier.iterdir())}


@pytest.mark.parametrize("mode", ["wal", "delete"])
def test_un_fichier_plus_recent_n_est_ni_reecrit_ni_retrograde(tmp_path: Path, mode: str) -> None:
    """Refusé sans être touché : ni ses octets, ni sa date, ni son mode, ni une sauvegarde.

    En mode `delete`, le passage en WAL de l'ouverture réécrirait l'en-tête : la
    version est lue **avant**, et c'est ce que l'empreinte prouve.
    """
    fichier = tmp_path / "journal.sqlite3"
    fichier_plus_recent(fichier, mode=mode)
    avant = empreinte(tmp_path)

    with pytest.raises(SchemaPlusRecent) as refus:
        relire(SqliteEventLog(fichier))
    with pytest.raises(SchemaPlusRecent):
        plan_acquis(fichier)

    assert empreinte(tmp_path) == avant
    assert version_de(fichier) == SCHEMA_VERSION + 1
    assert (refus.value.version, refus.value.connue) == (SCHEMA_VERSION + 1, SCHEMA_VERSION)
    assert refus.value.geste.startswith("mettre Maestro à jour")


def test_le_demarrage_refuse_un_fichier_plus_recent_en_disant_de_mettre_a_jour(
    tmp_path: Path,
) -> None:
    fichier = tmp_path / "journal.sqlite3"
    fichier_plus_recent(fichier, mode="wal")
    avant = empreinte(tmp_path)
    app = create_app(
        bus=InMemoryEventBus(), state=ControlTowerState(), event_log=SqliteEventLog(fichier)
    )

    with TestClient(app) as client:
        magasin = client.get("/api/sante").json()["magasin"]
        taches = client.get("/api/taches?projet=tous")

    assert magasin["disponible"] is False
    assert magasin["titre"] == "Journal local écrit par une version plus récente de Maestro"
    assert f"schéma v{SCHEMA_VERSION + 1}" in magasin["motif"]
    assert "Il n'a pas été touché" in magasin["motif"]
    assert magasin["geste"].startswith("mettre Maestro à jour")
    assert taches.status_code == 503
    assert empreinte(tmp_path) == avant
