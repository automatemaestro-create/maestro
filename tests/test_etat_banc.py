"""L'état qu'un passage du banc a laissé se rouvre sur une stack réelle (#1164).

Le critère : l'état d'un passage du banc (#1148) **se rouvre** sur une stack
réelle, **par l'API réelle**, **sans rien rejouer** ; on le rejoue à la demande, et
**son âge est dit**. Ce fichier le prouve en quatre temps, sans modèle ni réseau :

① **Le cycle entier** — un passage laisse un run et un projet sur le banc, l'état
   est sauvé, le banc remis à neuf, l'état rouvert : une API réelle (`create_app`
   sur les objets Redis de production) qui démarre sur le banc les sert. Aucun
   modèle n'est appelé — la garde du conftest (#782) ferait rougir le test.

② **Ce que l'état contient** — le banc et lui seul : ni les données de la copie,
   ni les battements, et un état à moitié écrit ne se rouvre jamais.

③ **Ce qui se dit** — l'âge, les verdicts, le geste pour rejouer.

④ **La ligne de commande** que `start.sh --etat-banc` joue : ses refus (rien à
   rouvrir, quelque chose de vivant sur le banc) et ses gestes.

Un faux Redis partagé (`tests/redis_factice.py`) tient lieu d'instance ; les dépôts
de fichiers vivent sous `tmp_path`, jamais sous le `core/` du dépôt qui joue la suite.
"""

from __future__ import annotations

import io
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis_factice import ClientSynchrone, ServeurFactice, brancher

from maestro.agents.rangement import SEGMENT_PROJETS
from maestro.controltower.app import create_app
from maestro.controltower.battement import RegistreBattementsRedis, batteur_redis
from maestro.controltower.bridge import publieur_redis
from maestro.controltower.donnees import DEPOTS, Donnees, donnees_du_banc
from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_EXECUTION_STATUT,
    ROLE_RUN,
    Event,
    RedisEventBus,
)
from maestro.controltower.persistence import RedisEventLog
from maestro.controltower.state import ControlTowerState
from maestro.espace import COMMUN, VARIABLE_ESPACE, Espace
from maestro.messaging.mailbox import RedisMailbox
from maestro.projets.store import ProjetStore
from maestro.scenarios import etat
from maestro.scenarios.modele import Rapport, Resultat
from maestro.scenarios.projets import VARIABLE_ATELIER

PASSAGE = "20260922-100639"


@pytest.fixture()
def serveur(monkeypatch: pytest.MonkeyPatch) -> ServeurFactice:
    return brancher(monkeypatch)


@pytest.fixture()
def client(serveur: ServeurFactice) -> ClientSynchrone:
    return ClientSynchrone(serveur)


@pytest.fixture(autouse=True)
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice : sous Windows, `tmp_path` vit dans `AppData`,
    que la déclaration d'un projet refuse à raison (même isolation que `test_projets.py`)."""
    dossier = tmp_path / "maison"
    dossier.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: dossier))
    return dossier


@pytest.fixture()
def ateliers(maison: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Où naissent les ateliers des passages — et donc leurs états sauvés."""
    racine = maison / "maestro-scenarios"
    monkeypatch.setenv(VARIABLE_ATELIER, str(racine))
    return racine


@pytest.fixture()
def copie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Une copie de travail : ses dépôts sous `core/`, son banc sous `.maestro/banc/`.

    Ses dépôts sont posés dans l'environnement, comme une API les lirait : c'est
    la configuration que `--vider` recopie sur le banc.
    """
    racine = tmp_path / "copie"
    for depot in DEPOTS:
        monkeypatch.setenv(depot.variable, str(racine / "core" / depot.nom))
    return racine


@pytest.fixture()
def banc(copie: Path) -> Donnees:
    return donnees_du_banc(racine=copie)


def sur(monkeypatch: pytest.MonkeyPatch, donnees: Donnees) -> None:
    """Place le test sur ces données — ce que `maestro-api --etat-banc` pose sur son process."""
    for variable, valeur in donnees.environnement().items():
        monkeypatch.setenv(variable, valeur)


def statut(run_id: str, valeur: str = "reussie") -> Event:
    return Event(
        type=EVENEMENT_EXECUTION_STATUT, run_id=run_id, agent=ACTEUR_RUN, role=ROLE_RUN,
        statut=valeur,
    )


def rapport(*verdicts: tuple[str, str], horodatage: str = PASSAGE) -> Rapport:
    return Rapport(
        horodatage=horodatage,
        resultats=tuple(
            Resultat(
                identifiant=ident, titre=f"scénario {ident}", verdict=verdict, motif="…",
                duree_s=600.0, run_id=f"run-{ident.lower()}", cout_usd=0.25,
            )
            for ident, verdict in verdicts
        ),
    )


def jouer_un_passage(
    monkeypatch: pytest.MonkeyPatch, banc: Donnees, maison: Path, run_id: str = "run-s2"
) -> str:
    """Ce qu'un passage laisse sur le banc : un projet déclaré, un run publié par son hôte."""
    sur(monkeypatch, banc)
    projet = ProjetStore.default().creer("dépensio", maison / "s2", origine="nouveau")
    publieur_redis()(statut(run_id))
    return projet.id


def api_sur(monkeypatch: pytest.MonkeyPatch, donnees: Donnees) -> Any:
    """Une API réelle — les objets Redis de production — placée sur ces données."""
    sur(monkeypatch, donnees)
    return create_app(
        bus=RedisEventBus(),
        mailbox=RedisMailbox(),
        event_log=RedisEventLog(),
        battements=RegistreBattementsRedis(),
        state=ControlTowerState(),
    )


def ce_que_sert(app: Any) -> tuple[set[str], set[str]]:
    """Les runs et les projets que l'API rend — ce que l'écran montrerait."""
    with TestClient(app) as api:
        runs = api.get("/api/executions?projet=tous")
        projets = api.get("/api/projets")
        assert runs.status_code == 200, runs.text
        assert projets.status_code == 200, projets.text
        return (
            {r["run_id"] for r in runs.json()},
            {p["nom"] for p in projets.json()},
        )


# ── ① Le cycle entier ────────────────────────────────────────────────────────


def test_un_passage_se_rouvre_sur_l_api_reelle_sans_rien_rejouer(
    monkeypatch: pytest.MonkeyPatch,
    client: ClientSynchrone,
    banc: Donnees,
    copie: Path,
    maison: Path,
    ateliers: Path,
) -> None:
    # Les données de la copie vivent à côté, dans leur propre espace : jamais rouvertes.
    monkeypatch.setenv(VARIABLE_ESPACE, COMMUN)
    publieur_redis()(statut("run-du-poste"))

    jouer_un_passage(monkeypatch, banc, maison)
    atelier = ateliers / PASSAGE
    atelier.mkdir(parents=True)
    etat.sauver(rapport(("S2", "vert")), atelier, banc, client)

    copie_donnees = Donnees(
        espace=Espace(COMMUN, "clone principal"),
        racines={d.nom: copie / "core" / d.nom for d in DEPOTS},
    )
    etat.vider(banc, copie_donnees, client)
    assert ce_que_sert(api_sur(monkeypatch, banc)) == (set(), set()), "remis à neuf"

    instantane = etat.dernier(ateliers)
    assert instantane is not None and instantane.passage == PASSAGE
    etat.rouvrir(instantane, banc, client)

    assert ce_que_sert(api_sur(monkeypatch, banc)) == ({"run-s2"}, {"dépensio"})
    # Rouvrir deux fois rend le même état : chaque ouverture repart de la sauvegarde.
    etat.rouvrir(instantane, banc, client)
    assert ce_que_sert(api_sur(monkeypatch, banc)) == ({"run-s2"}, {"dépensio"})


# ── ② Ce que l'état contient ─────────────────────────────────────────────────


def test_l_etat_ne_porte_que_le_banc_et_pas_ses_battements(
    monkeypatch: pytest.MonkeyPatch,
    client: ClientSynchrone,
    banc: Donnees,
    maison: Path,
    ateliers: Path,
) -> None:
    monkeypatch.setenv(VARIABLE_ESPACE, COMMUN)
    publieur_redis()(statut("run-du-poste"))
    jouer_un_passage(monkeypatch, banc, maison)
    batteur_redis()("run-s2")  # un run que le passage laisse en vol

    atelier = ateliers / PASSAGE
    atelier.mkdir(parents=True)
    instantane = etat.sauver(rapport(("S2", "vert")), atelier, banc, client)

    lignes = (instantane.dossier / etat.FICHIER_JOURNAL).read_text(encoding="utf-8").splitlines()
    runs = {json.loads(json.loads(ligne))["run_id"] for ligne in lignes}
    assert runs == {"run-s2"}, "le journal du poste n'entre pas dans l'état du banc"
    assert instantane.evenements == len(lignes) == 1
    assert instantane.projets == 1

    etat.rouvrir(instantane, banc, client)
    battements = etat.cles_du_banc(banc)[1]
    assert client.hgetall(battements) == {}, "un battement est un signal de vie, pas de l'état"


def test_un_etat_a_moitie_ecrit_ne_se_rouvre_jamais(
    client: ClientSynchrone, banc: Donnees, ateliers: Path
) -> None:
    """Sauver écrit à côté puis renomme : un passage coupé ne laisse qu'un `.partiel`."""
    coupe = ateliers / PASSAGE / f"{etat.DOSSIER_ETAT}.partiel"
    coupe.mkdir(parents=True)
    (coupe / etat.FICHIER_META).write_text('{"version": 1}', encoding="utf-8")
    assert etat.dernier(ateliers) is None


def test_sauver_deux_fois_remplace_l_etat_du_passage(
    client: ClientSynchrone, banc: Donnees, ateliers: Path
) -> None:
    atelier = ateliers / PASSAGE
    atelier.mkdir(parents=True)
    etat.sauver(rapport(("S1", "rouge")), atelier, banc, client)
    etat.sauver(rapport(("S1", "vert")), atelier, banc, client)
    instantane = etat.dernier(ateliers)
    assert instantane is not None and instantane.scenarios == (("S1", "vert"),)
    assert not (atelier / f"{etat.DOSSIER_ETAT}.partiel").exists()


def test_le_dernier_etat_est_le_plus_recent_et_une_autre_forme_est_ecartee(
    client: ClientSynchrone, banc: Donnees, ateliers: Path
) -> None:
    maintenant = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    for passage, decalage in (("20260920-090000", 48), ("20260922-100000", 2)):
        atelier = ateliers / passage
        atelier.mkdir(parents=True)
        etat.sauver(
            rapport(("S1", "vert"), horodatage=passage), atelier, banc, client,
            maintenant=maintenant - timedelta(hours=decalage),
        )
    # Un état d'une forme future n'est pas rouvert de travers : il est ignoré.
    futur = ateliers / "20260923-000000" / etat.DOSSIER_ETAT
    futur.mkdir(parents=True)
    (futur / etat.FICHIER_META).write_text(
        json.dumps({"version": 99, "passage": "x", "sauve_le": maintenant.isoformat()}),
        encoding="utf-8",
    )
    (ateliers / "passage-sans-etat").mkdir()

    instantane = etat.dernier(ateliers)
    assert instantane is not None and instantane.passage == "20260922-100000"


def test_vider_garde_la_configuration_de_la_copie_sans_ses_projets(
    client: ClientSynchrone, banc: Donnees, copie: Path
) -> None:
    """Un banc neuf : la configuration de la copie, rien de ce que ses projets ont réglé."""
    core = copie / "core"
    (core / "permissions" / SEGMENT_PROJETS / "prj-abcd1234").mkdir(parents=True)
    (core / "permissions" / "qa.json").write_text("{}", encoding="utf-8")
    (core / "permissions" / SEGMENT_PROJETS / "prj-abcd1234" / "qa.json").write_text(
        "{}", encoding="utf-8"
    )
    (core / "chat").mkdir(parents=True)
    (core / "chat" / "orchestrateur.jsonl").write_text("{}\n", encoding="utf-8")
    client.rpush(etat.cles_du_banc(banc)[0], "vieux")
    copie_donnees = Donnees(
        espace=Espace(COMMUN, "clone principal"),
        racines={d.nom: core / d.nom for d in DEPOTS},
    )

    etat.vider(banc, copie_donnees, client)

    permissions = banc.racines["permissions"]
    assert (permissions / "qa.json").is_file()
    assert not (permissions / SEGMENT_PROJETS).exists()
    assert list(banc.racines["chat"].iterdir()) == [], "aucun fil"
    assert client.lrange(etat.cles_du_banc(banc)[0], 0, -1) == [], "aucun run"


# ── ③ Ce qui se dit ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("secondes", "attendu"),
    [
        (2, "à l'instant"),
        (45, "il y a 45 s"),
        (12 * 60 + 5, "il y a 12 min"),
        (3 * 3600 + 5 * 60, "il y a 3 h 05 min"),
        (2 * 86400 + 4 * 3600 + 59, "il y a 2 j 4 h"),
    ],
)
def test_l_age_se_dit_a_la_precision_qui_compte(secondes: float, attendu: str) -> None:
    assert etat.age_lisible(secondes) == attendu


def test_la_reouverture_dit_le_passage_son_age_ses_verdicts_et_le_rejeu(
    client: ClientSynchrone, banc: Donnees, ateliers: Path
) -> None:
    sauve_le = datetime(2026, 9, 22, 10, 44, tzinfo=UTC)
    atelier = ateliers / PASSAGE
    atelier.mkdir(parents=True)
    instantane = etat.sauver(
        rapport(("S1", "rouge"), ("S2", "vert")), atelier, banc, client, maintenant=sauve_le
    )

    lignes = etat.annonce(instantane, maintenant=sauve_le + timedelta(hours=2, minutes=13))
    texte = "\n".join(lignes)

    assert f"passage {PASSAGE}" in lignes[0]
    assert "il y a 2 h 13 min" in lignes[0]
    assert "2026-09-22 10:44 UTC" in lignes[0]
    assert "S1 rouge · S2 vert" in texte
    assert etat.GESTE_REJOUER in texte
    assert "20 min, 0,50 $" in texte, "le prix du rejeu est celui qu'a coûté ce passage"


# ── ④ La ligne de commande ───────────────────────────────────────────────────


def commande(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    *,
    client: ClientSynchrone,
    copie: Path,
    ateliers: Path,
    sonde_api: bool = False,
) -> tuple[int, str, str]:
    """`python -m maestro.scenarios.etat …`, sans Redis réel ni API.

    `--verifier` place le process sur le banc : on laisse `monkeypatch` défaire ce
    qu'il pose, pour que le test suivant parte de l'environnement d'avant.
    """
    for variable in donnees_du_banc(racine=copie).environnement():
        monkeypatch.setenv(variable, os.environ.get(variable, ""))
    sortie, erreur = io.StringIO(), io.StringIO()
    code = etat.main(
        args,
        client=client,
        verifier_redis=lambda: 0,
        sonde_api=lambda: sonde_api,
        copie=copie,
        racine_ateliers=ateliers,
        sortie=sortie,
        erreur=erreur,
    )
    return code, sortie.getvalue(), erreur.getvalue()


def test_rien_a_rouvrir_est_un_refus_qui_donne_le_geste(
    monkeypatch: pytest.MonkeyPatch, client: ClientSynchrone, copie: Path, ateliers: Path
) -> None:
    code, _sortie, erreur = commande(
        monkeypatch, ["--verifier"], client=client, copie=copie, ateliers=ateliers
    )
    assert code == etat.CODE_AUCUN_ETAT
    assert etat.GESTE_REJOUER in erreur


def test_le_preflight_dit_l_age_de_l_etat_a_rouvrir(
    monkeypatch: pytest.MonkeyPatch,
    client: ClientSynchrone,
    banc: Donnees,
    copie: Path,
    ateliers: Path,
) -> None:
    atelier = ateliers / PASSAGE
    atelier.mkdir(parents=True)
    etat.sauver(rapport(("S4", "vert")), atelier, banc, client)

    code, sortie, _erreur = commande(
        monkeypatch, ["--verifier"], client=client, copie=copie, ateliers=ateliers
    )
    assert code == etat.CODE_FAIT
    assert f"passage {PASSAGE}, sauvé à l'instant" in sortie


def test_rejouer_ne_demande_aucun_etat(
    monkeypatch: pytest.MonkeyPatch, client: ClientSynchrone, copie: Path, ateliers: Path
) -> None:
    code, sortie, _erreur = commande(
        monkeypatch, ["--verifier", "--rejouer"], client=client, copie=copie, ateliers=ateliers
    )
    assert code == etat.CODE_FAIT
    assert "repart à neuf" in sortie


def test_un_run_en_vol_sur_le_banc_fait_refuser(
    monkeypatch: pytest.MonkeyPatch,
    client: ClientSynchrone,
    banc: Donnees,
    copie: Path,
    ateliers: Path,
) -> None:
    """Il republierait dans le journal qu'on réécrit : même règle que la purge."""
    client.hset(etat.cles_du_banc(banc)[1], "run-vivant", datetime.now(UTC).isoformat())
    for args in (["--rouvrir"], ["--vider"], ["--verifier", "--rejouer"]):
        code, _sortie, erreur = commande(
            monkeypatch, args, client=client, copie=copie, ateliers=ateliers
        )
        assert code == etat.CODE_REFUS, args
        assert "run-vivant" in erreur


def test_une_api_qui_sert_le_banc_fait_refuser(
    monkeypatch: pytest.MonkeyPatch, client: ClientSynchrone, copie: Path, ateliers: Path
) -> None:
    code, _sortie, erreur = commande(
        monkeypatch, ["--vider"], client=client, copie=copie, ateliers=ateliers, sonde_api=True
    )
    assert code == etat.CODE_REFUS
    assert etat.GESTE_ARRETER in erreur


def test_rouvrir_par_la_commande_remet_l_etat_du_dernier_passage(
    monkeypatch: pytest.MonkeyPatch,
    client: ClientSynchrone,
    banc: Donnees,
    copie: Path,
    maison: Path,
    ateliers: Path,
) -> None:
    jouer_un_passage(monkeypatch, banc, maison)
    atelier = ateliers / PASSAGE
    atelier.mkdir(parents=True)
    etat.sauver(rapport(("S2", "vert")), atelier, banc, client)
    client.delete(*etat.cles_du_banc(banc))

    code, sortie, _erreur = commande(
        monkeypatch, ["--rouvrir"], client=client, copie=copie, ateliers=ateliers
    )
    assert code == etat.CODE_FAIT
    assert "rien n'est rejoué" in sortie
    assert len(client.lrange(etat.cles_du_banc(banc)[0], 0, -1)) == 1


@pytest.mark.parametrize(
    "args", [[], ["--rouvrir", "--vider"], ["--rouvrir", "--rejouer"], ["--verifier", "--x"]]
)
def test_un_geste_mal_forme_est_un_usage(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    client: ClientSynchrone,
    copie: Path,
    ateliers: Path,
) -> None:
    code, _sortie, _erreur = commande(
        monkeypatch, args, client=client, copie=copie, ateliers=ateliers
    )
    assert code == etat.CODE_USAGE
