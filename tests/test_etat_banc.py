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

⑤ **Les passages que le banc garde** (#1457) — les derniers, et celui dont l'état
   se rouvre quel que soit son rang ; un passage retiré emporte les worktrees que
   ses tâches ont laissés sous la racine jetable ; l'ancien atelier
   (`~/maestro-scenarios`) est repris par la même règle, puis retiré.

Un faux Redis partagé (`tests/redis_factice.py`) tient lieu d'instance ; les dépôts
de fichiers vivent sous `tmp_path`, jamais sous le `core/` du dépôt qui joue la suite.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
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
from maestro.emplacements import TEMOIN_PID, occupant
from maestro.espace import COMMUN, VARIABLE_ESPACE, Espace
from maestro.messaging.mailbox import RedisMailbox
from maestro.projets.store import ProjetStore
from maestro.sandbox.ramassage import porte_un_worktree
from maestro.scenarios import etat
from maestro.scenarios.modele import Rapport, Resultat
from maestro.scenarios.projets import (
    VARIABLE_ATELIER,
    VARIABLE_PASSAGES_GARDES,
    Atelier,
    retenir,
    worktrees_laisses,
)

PASSAGE = "20260922-100639"
GIT = shutil.which("git")
besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")


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
    """Où naissent les ateliers des passages — et donc leurs états sauvés.

    Un atelier **réglé**, ni le défaut ni l'ancien (`~/maestro-scenarios`, #1457) :
    ceux-là s'éprouvent sans la variable."""
    racine = maison / "ateliers-du-banc"
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


def test_une_stack_neuve_ne_demande_aucun_etat_et_ne_promet_aucun_passage(
    monkeypatch: pytest.MonkeyPatch, client: ClientSynchrone, copie: Path, ateliers: Path
) -> None:
    """`start.sh --etat-neuf` (#1165) : le banc vidé et servi tel quel. Rien à rouvrir n'est pas
    un refus ici, et l'annonce ne parle pas d'un passage qui n'aura pas lieu."""
    code, sortie, _erreur = commande(
        monkeypatch, ["--verifier", "--neuf"], client=client, copie=copie, ateliers=ateliers
    )
    assert code == etat.CODE_FAIT
    assert "stack neuve" in sortie
    assert "rejoue" not in sortie, "aucun passage ne suit une stack neuve"


def test_un_run_en_vol_sur_le_banc_fait_refuser(
    monkeypatch: pytest.MonkeyPatch,
    client: ClientSynchrone,
    banc: Donnees,
    copie: Path,
    ateliers: Path,
) -> None:
    """Il republierait dans le journal qu'on réécrit : même règle que la purge."""
    client.hset(etat.cles_du_banc(banc)[1], "run-vivant", datetime.now(UTC).isoformat())
    for args in (["--rouvrir"], ["--vider"], ["--verifier", "--rejouer"], ["--verifier", "--neuf"]):
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


class RedisInterdit:
    """Un client Redis qu'on n'a pas le droit de toucher : tout accès fait rougir."""

    def __getattr__(self, nom: str) -> Any:
        raise AssertionError(f"--decrire a demandé Redis ({nom}) : il ne lit que le disque")


def test_decrire_rend_l_etat_a_rouvrir_sans_toucher_a_redis(
    monkeypatch: pytest.MonkeyPatch,
    client: ClientSynchrone,
    banc: Donnees,
    copie: Path,
    ateliers: Path,
) -> None:
    """Ce qu'une présentation de jalon dit de ce qu'elle montre (#1166) : le passage, sa date,
    son âge, ses verdicts — relus en JSON, sans Redis ni banc à interroger."""
    atelier = ateliers / PASSAGE
    atelier.mkdir(parents=True)
    sauve = datetime(2026, 9, 22, 10, 44, tzinfo=UTC)
    etat.sauver(rapport(("S1", "rouge"), ("S2", "vert")), atelier, banc, client, maintenant=sauve)

    sortie, erreur = io.StringIO(), io.StringIO()
    code = etat.main(
        ["--decrire"],
        client=RedisInterdit(),  # type: ignore[arg-type]
        copie=copie,
        racine_ateliers=ateliers,
        maintenant=sauve + timedelta(hours=3),
        sortie=sortie,
        erreur=erreur,
    )

    assert code == etat.CODE_FAIT, erreur.getvalue()
    lignes = sortie.getvalue().splitlines()
    assert len(lignes) == 1, "une ligne : elle se redirige telle quelle vers un fichier"
    decrit = json.loads(lignes[0])
    assert decrit["passage"] == PASSAGE
    assert datetime.fromisoformat(decrit["sauve_le"]) == sauve
    assert decrit["age_s"] == 3 * 3600
    assert decrit["scenarios"] == [
        {"id": "S1", "verdict": "rouge"},
        {"id": "S2", "verdict": "vert"},
    ]


def test_decrire_sans_etat_est_le_meme_refus_que_rouvrir(copie: Path, ateliers: Path) -> None:
    sortie, erreur = io.StringIO(), io.StringIO()
    code = etat.main(
        ["--decrire"],
        client=RedisInterdit(),  # type: ignore[arg-type]
        copie=copie,
        racine_ateliers=ateliers,
        sortie=sortie,
        erreur=erreur,
    )
    assert code == etat.CODE_AUCUN_ETAT
    assert sortie.getvalue() == "", "rien sur la sortie : un fichier redirigé resterait vide"
    assert etat.GESTE_REJOUER in erreur.getvalue()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--rouvrir", "--vider"],
        ["--rouvrir", "--rejouer"],
        ["--verifier", "--x"],
        ["--vider", "--neuf"],
        ["--verifier", "--rejouer", "--neuf"],
        ["--decrire", "--rejouer"],
        ["--decrire", "--rouvrir"],
        ["--decrire", "--neuf"],
    ],
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


# ── ⑤ Les passages que le banc garde (#1457) ─────────────────────────────────


@pytest.fixture()
def temporaire(tmp_path: Path) -> Path:
    """Le répertoire temporaire du poste, à part : la racine jetable vit dessous."""
    dossier = tmp_path / "temp"
    dossier.mkdir()
    return dossier


def regime(ateliers: Path | None, temporaire: Path, gardes: int) -> dict[str, str]:
    """L'environnement d'une rétention : son atelier (ou l'atelier par défaut), son temporaire."""
    env = {VARIABLE_PASSAGES_GARDES: str(gardes), "TMPDIR": str(temporaire)}
    if ateliers is not None:
        env[VARIABLE_ATELIER] = str(ateliers)
    return env


def un_passage(racine: Path, nom: str) -> Path:
    """Un passage tel que le banc le laisse : un dossier par scénario joué."""
    dossier = racine / nom
    (dossier / "s1-vider").mkdir(parents=True)
    return dossier


def pid_mort() -> int:
    """Le pid d'un process qui vient de finir — personne ne l'occupe plus."""
    fini = subprocess.Popen([sys.executable, "-c", "pass"])
    fini.wait()
    return fini.pid


def git(racine: Path, *arguments: str) -> None:
    assert GIT is not None
    subprocess.run(  # noqa: S603 - git, sur un dépôt du test
        [GIT, "-c", "user.email=t@t", "-c", "user.name=T", "-c", "core.hooksPath=", *arguments],
        cwd=racine,
        check=True,
        capture_output=True,
    )


def projet_versionne(racine: Path) -> Path:
    """Un projet du passage sous Git, comme le banc en déclare (S12)."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / "app.py").write_text("print('salut')\n", encoding="utf-8")
    git(racine, "init", "--quiet")
    git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    git(racine, "add", "-A")
    git(racine, "commit", "--quiet", "-m", "socle")
    return racine


def test_le_banc_ne_garde_que_ses_derniers_passages_et_celui_qu_on_rouvre(
    client: ClientSynchrone, banc: Donnees, ateliers: Path, temporaire: Path
) -> None:
    """Le critère : les derniers passages, et **jamais** celui que `start.sh --etat-banc`
    rouvre — le rejouer coûte du vrai modèle. Ce qui n'a pas la forme d'un passage (un essai
    posé là à la main) n'est pas à la rétention."""
    noms = [f"2026100{jour}-100000" for jour in range(1, 9)]
    for nom in noms:
        un_passage(ateliers, nom)
    etat.sauver(rapport(("S2", "vert"), horodatage=noms[1]), ateliers / noms[1], banc, client)
    (ateliers / "essai-1158").mkdir()
    rouvert = etat.dernier(ateliers)
    assert rouvert is not None and rouvert.passage == noms[1]

    retention = retenir(
        rouvert=rouvert.dossier.parent, environnement=regime(ateliers, temporaire, 3)
    )

    assert sorted(d.name for d in ateliers.iterdir()) == sorted(
        [noms[1], *noms[-3:], "essai-1158"]
    )
    assert set(retention.retires) == {noms[0], noms[2], noms[3], noms[4]}
    assert retention.rouvert == noms[1]
    apres = etat.dernier(ateliers)
    assert apres is not None and apres.passage == noms[1], "toujours rouvrable sans rien rejouer"
    texte = "\n".join(retention.lignes())
    assert "4 passage(s) retiré(s) au-delà des 3 derniers gardés" in texte
    assert f"{noms[1]} gardé pour start.sh --etat-banc" in texte


def test_l_ordre_des_passages_est_celui_de_leur_rang_et_non_du_texte(
    ateliers: Path, temporaire: Path
) -> None:
    """`…-10` vient après `…-9` : l'ordre lexical les inverserait, et garderait le mauvais."""
    for nom in ("20261009-100000-9", "20261009-100000-10"):
        un_passage(ateliers, nom)
    retention = retenir(environnement=regime(ateliers, temporaire, 1))
    assert retention.retires == ("20261009-100000-9",)
    assert [d.name for d in ateliers.iterdir()] == ["20261009-100000-10"]


def test_un_passage_en_cours_n_est_jamais_retire(ateliers: Path, temporaire: Path) -> None:
    """L'atelier est celui du poste : une autre copie peut jouer un passage plus ancien que les
    derniers. Le banc se nomme dans le passage qu'il réserve, et ce nom le garde."""
    reserve = Atelier.reserver("20261001-100000", environnement=regime(ateliers, temporaire, 1))
    assert occupant(reserve.racine) == os.getpid(), "le banc s'est nommé dans son passage"
    abandonne = un_passage(ateliers, "20261002-100000")
    (abandonne / TEMOIN_PID).write_text(str(pid_mort()), encoding="utf-8")
    un_passage(ateliers, "20261003-100000")

    retention = retenir(environnement=regime(ateliers, temporaire, 1))

    assert retention.occupes == ("20261001-100000",)
    assert retention.retires == ("20261002-100000",), "son banc est mort : il part"
    assert reserve.racine.is_dir()
    assert "1 encore en cours, gardé(s)" in "\n".join(retention.lignes())


def test_une_retention_eteinte_ne_retire_rien(ateliers: Path, temporaire: Path) -> None:
    for jour in range(1, 4):
        un_passage(ateliers, f"2026100{jour}-100000")
    retention = retenir(environnement=regime(ateliers, temporaire, 0))
    assert retention.retires == () and retention.lignes() == []
    assert len(list(ateliers.iterdir())) == 3


@besoin_de_git
def test_retirer_un_passage_retire_les_worktrees_que_ses_taches_ont_laisses(
    tmp_path: Path, ateliers: Path, temporaire: Path
) -> None:
    """Le défaut constaté : le ramassage garde un worktree **tant que son dépôt existe** — c'est
    son refus de toujours —, et le dépôt est un projet du banc, gardé à vie avec son passage. Les
    espaces de ses tâches restaient donc sous la racine jetable pour toujours. Le passage retiré
    les emporte ; un espace qu'une tâche vivante occupe garde son passage, et un worktree que la
    personne aurait ouvert ailleurs n'est jamais touché."""
    env = regime(ateliers, temporaire, 1)
    jetables = temporaire / "maestro"

    vieux = un_passage(ateliers, "20261001-100000")
    depot = projet_versionne(vieux / "s12-reprise")
    laisse = jetables / f"maestro-dev-pid{pid_mort()}-abcd1234" / "t-1"
    laisse.parent.mkdir(parents=True)
    git(depot, "worktree", "add", "--quiet", "-b", "maestro/t-1", str(laisse))
    ailleurs = tmp_path / "mes-worktrees" / "essai"
    git(depot, "worktree", "add", "--quiet", "-b", "essai", str(ailleurs))

    en_vol = un_passage(ateliers, "20261002-100000")
    depot_en_vol = projet_versionne(en_vol / "s12-reprise")
    occupe = jetables / f"maestro-dev-pid{os.getpid()}-ffff0000" / "t-2"
    occupe.parent.mkdir(parents=True)
    git(depot_en_vol, "worktree", "add", "--quiet", "-b", "maestro/t-2", str(occupe))
    un_passage(ateliers, "20261003-100000")

    assert porte_un_worktree(laisse.parent), "le ramassage le garderait : son dépôt existe"
    assert worktrees_laisses(vieux, env) == (laisse.parent,)

    retention = retenir(environnement=env)

    assert retention.retires == ("20261001-100000",)
    assert retention.worktrees == 1
    assert not vieux.exists()
    assert not laisse.parent.exists(), "l'espace de la tâche part avec son passage"
    assert ailleurs.is_dir(), "un worktree ouvert hors de la racine jetable n'est pas au banc"
    assert retention.occupes == ("20261002-100000",), "une tâche y travaille encore"
    assert occupe.is_dir() and en_vol.is_dir()
    assert "avec 1 worktree(s) de tâches sous la racine jetable" in "\n".join(
        retention.lignes()
    )


def test_l_ancien_atelier_est_repris_puis_retire_quand_il_ne_porte_plus_rien(
    monkeypatch: pytest.MonkeyPatch,
    client: ClientSynchrone,
    banc: Donnees,
    maison: Path,
    temporaire: Path,
) -> None:
    """Le premier passage après la mise à jour : `~/maestro-scenarios` est repris par la même
    règle — ses plus récents comptent parmi les derniers, son état reste rouvrable sans rien
    rejouer —, ce qui n'est pas un passage y est nommé sans être touché, et il disparaît quand
    il ne porte plus rien. Le passage le dit, à chaque fois."""
    monkeypatch.delenv(VARIABLE_ATELIER, raising=False)
    ancien = maison / "maestro-scenarios"
    neuf = maison / ".maestro" / "ateliers" / "scenarios"
    for nom in (PASSAGE, "20260923-100000", "20260924-100000"):
        un_passage(ancien, nom)
    etat.sauver(rapport(("S2", "vert")), ancien / PASSAGE, banc, client)
    (ancien / "essai-1158").mkdir()
    rouvert = etat.dernier()
    assert rouvert is not None and rouvert.passage == PASSAGE, "l'état d'avant se rouvre encore"

    env = regime(None, temporaire, 2)
    atelier = Atelier.reserver("20261009-100000", environnement=env)
    assert atelier.racine.parent == neuf, "le passage neuf naît sous ~/.maestro/ateliers"
    retention = retenir(rouvert=rouvert.dossier.parent, environnement=env)

    assert retention.retires == ("20260923-100000",)
    assert sorted(d.name for d in ancien.iterdir()) == [
        PASSAGE,
        "20260924-100000",
        "essai-1158",
    ]
    texte = "\n".join(retention.lignes())
    assert f"Ancien atelier {ancien} : repris par la rétention" in texte
    assert "2 de ses passages restent parmi les gardés" in texte
    assert "jamais touchés (à retirer à la main) : essai-1158" in texte

    # Le passage suivant sauve son état : l'ancien n'est plus celui qu'on rouvre, et part.
    (ancien / "essai-1158").rmdir()
    etat.sauver(rapport(("S2", "vert"), horodatage=atelier.passage), atelier.racine, banc, client)
    Atelier.reserver("20261009-110000", environnement=env)
    rouvert = etat.dernier()
    assert rouvert is not None and rouvert.passage == atelier.passage
    retention = retenir(rouvert=rouvert.dossier.parent, environnement=env)

    assert retention.ancienne_retiree and not ancien.exists()
    assert sorted(d.name for d in neuf.iterdir()) == ["20261009-100000", "20261009-110000"]
    assert f"Ancien atelier {ancien} retiré : il ne portait plus rien." in retention.lignes()


def test_un_atelier_regle_ne_lit_ni_ne_retire_l_ancien(
    maison: Path, ateliers: Path, temporaire: Path
) -> None:
    """Qui règle `MAESTRO_SCENARIOS_ATELIER` a choisi où vit son atelier : l'ancien défaut
    n'est plus le sien, et rien n'y est lu ni retiré."""
    ancien = maison / "maestro-scenarios"
    for jour in range(1, 4):
        un_passage(ancien, f"2026092{jour}-100000")
    un_passage(ateliers, "20261009-100000")
    retention = retenir(environnement=regime(ateliers, temporaire, 1))
    assert retention.retires == () and retention.ancienne is None
    assert len(list(ancien.iterdir())) == 3
    assert etat.dernier() is None, "aucun état sauvé sous l'atelier réglé"
