"""Une stack réelle par copie de travail : chacune ne voit que ses données (#1164).

Deux copies de travail d'un poste parlent au **même** Redis. Avant ce ticket,
elles s'y rangeaient sous les mêmes noms : une relecture lancée depuis un worktree
rejouait au démarrage le journal du clone principal, et recevait en direct les
événements de ses runs. `maestro.espace` range chaque clé et chaque canal dans
l'espace de la copie ; ce fichier le prouve en trois temps.

① **Le nom** — `MAESTRO_ESPACE`, sinon le worktree de la copie, sinon `commun`
   (l'espace sans préfixe du clone principal). Lu sur de vrais fichiers `.git`
   écrits dans `tmp_path`, jamais sur le dépôt qui joue la suite.

② **La séparation, objet par objet** — deux stacks sur **un seul** faux serveur
   Redis : ce que l'une écrit ou publie, l'autre ne le lit ni ne le reçoit. C'est
   le point : le faux serveur partage ses canaux Pub/Sub entre tous ses clients,
   comme une vraie instance les partage entre toutes ses bases.

③ **La séparation, API comprise** — deux API réelles (`create_app` sur les
   objets Redis de production), un run publié par un hôte de la première : au
   démarrage, seule la première le rejoue.

Les fils et les projets vivent dans des fichiers, sous `core/` **de la copie** :
leur séparation ne doit rien à Redis, et ④ garde qu'elle suit la même copie que
l'espace — celle du code qui tourne.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import conftest
import pytest
from fastapi.testclient import TestClient
from redis_factice import ClientSynchrone, ServeurFactice, brancher

from maestro import espace as espace_module
from maestro.controltower.app import create_app
from maestro.controltower.battement import (
    CLE_BATTEMENTS,
    RegistreBattementsRedis,
    batteur_redis,
)
from maestro.controltower.bridge import publieur_redis
from maestro.controltower.chat import ChatStore
from maestro.controltower.events import (
    ACTEUR_RUN,
    CANAL_EVENEMENTS,
    EVENEMENT_EXECUTION_STATUT,
    ROLE_RUN,
    Event,
    RedisEventBus,
)
from maestro.controltower.persistence import CLE_JOURNAL_EVENEMENTS, RedisEventLog
from maestro.controltower.state import ControlTowerState
from maestro.espace import (
    COMMUN,
    ORIGINE_CLONE,
    ORIGINE_VARIABLE,
    ORIGINE_WORKTREE,
    VARIABLE_ESPACE,
    Espace,
    EspaceInvalide,
    espace_courant,
    nom_de_worktree,
    nom_redis,
)
from maestro.messaging.mailbox import MESSAGE_NOTIFICATION, AgentMessage, RedisMailbox
from maestro.projets.store import ProjetStore
from maestro.queue import celery_app

# ── ① Le nom ─────────────────────────────────────────────────────────────────


def copie(tmp_path: Path, contenu_git: str | None) -> Path:
    """Une copie de travail factice : un dossier `.git` (clone) ou un fichier (worktree)."""
    racine = tmp_path / "copie"
    racine.mkdir()
    if contenu_git is None:
        (racine / ".git").mkdir()
    else:
        (racine / ".git").write_text(contenu_git, encoding="utf-8")
    return racine


def test_le_clone_principal_est_l_espace_commun_sans_prefixe(tmp_path: Path) -> None:
    racine = copie(tmp_path, None)
    espace = espace_courant({}, racine=racine)
    assert espace == Espace(COMMUN, ORIGINE_CLONE)
    assert espace.prefixe == ""
    # Pas une copie qui lui ressemble : le nom lui-même — l'historique du poste
    # reste lisible sous les noms d'avant #1164.
    assert espace.nommer(CLE_JOURNAL_EVENEMENTS) is CLE_JOURNAL_EVENEMENTS


@pytest.mark.parametrize(
    "gitdir",
    [
        "E:/Projects Solutions/Maestro/.git/worktrees/1164-une-stack",
        "E:\\Projects Solutions\\Maestro\\.git\\worktrees\\1164-une-stack",
        "/maestro/depot/.git/worktrees/1164-une-stack",
    ],
)
def test_un_worktree_prend_son_nom_git(tmp_path: Path, gitdir: str) -> None:
    """Le nom du worktree, quel que soit l'OS qui a écrit le chemin — le filet CI
    monte un worktree Windows dans un conteneur Linux."""
    racine = copie(tmp_path, f"gitdir: {gitdir}\n")
    assert nom_de_worktree(racine) == "1164-une-stack"
    espace = espace_courant({}, racine=racine)
    assert espace == Espace("1164-une-stack", ORIGINE_WORKTREE)
    assert espace.nommer(CANAL_EVENEMENTS) == "1164-une-stack:maestro.evenements"


def test_un_worktree_nomme_commun_ne_lit_jamais_le_clone_principal(tmp_path: Path) -> None:
    racine = copie(tmp_path, "gitdir: /depot/.git/worktrees/commun\n")
    espace = espace_courant({}, racine=racine)
    assert espace.nom != COMMUN
    assert espace.prefixe, "un worktree préfixe toujours ses noms"


def test_un_nom_de_worktree_hors_alphabet_est_assaini(tmp_path: Path) -> None:
    racine = copie(tmp_path, "gitdir: /depot/.git/worktrees/mon essai:*\n")
    nom = espace_courant({}, racine=racine).nom
    assert nom == "mon-essai"
    assert ":" not in nom and "*" not in nom


def test_la_variable_prime_sur_la_copie(tmp_path: Path) -> None:
    """`start.sh` l'exporte : une stack démarrée n'a qu'un espace, quel que soit le
    répertoire où ses process tournent."""
    racine = copie(tmp_path, "gitdir: /depot/.git/worktrees/1164-une-stack\n")
    espace = espace_courant({VARIABLE_ESPACE: "autre"}, racine=racine)
    assert espace == Espace("autre", ORIGINE_VARIABLE)


def test_une_variable_vide_vaut_absence(tmp_path: Path) -> None:
    racine = copie(tmp_path, None)
    assert espace_courant({VARIABLE_ESPACE: "  "}, racine=racine).nom == COMMUN


@pytest.mark.parametrize("nom", ["a:b", "a b", "a*", "-a", "é", "x" * 129])
def test_une_variable_irrecevable_est_refusee_jamais_corrigee(nom: str) -> None:
    with pytest.raises(EspaceInvalide, match=VARIABLE_ESPACE):
        espace_courant({VARIABLE_ESPACE: nom})


def test_le_conftest_fige_l_espace_commun() -> None:
    """Le conftest écrit la variable et sa valeur au lieu de les importer (il se
    charge avant le paquet) : on les confronte ici, pour qu'un renommage se voie."""
    assert conftest.CLE_ESPACE == VARIABLE_ESPACE
    assert conftest.ESPACE_DES_TESTS == COMMUN
    assert espace_courant().nom == COMMUN


# ── Un faux serveur Redis, partagé (`tests/redis_factice.py`) ────────────────


@pytest.fixture()
def serveur(monkeypatch: pytest.MonkeyPatch) -> ServeurFactice:
    """Une seule instance Redis pour toutes les stacks du test."""
    return brancher(monkeypatch)


def dans(monkeypatch: pytest.MonkeyPatch, espace: str) -> None:
    """Se place dans la stack `espace` : ce que `start.sh` exporte à son API."""
    monkeypatch.setenv(VARIABLE_ESPACE, espace)


def statut_de_run(run_id: str) -> Event:
    """Ce qu'un hôte détaché publie de son run — le fait que l'écran rejoue."""
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=run_id,
        agent=ACTEUR_RUN,
        role=ROLE_RUN,
        statut="en_cours",
    )


# ── ② La séparation, objet par objet ─────────────────────────────────────────


def test_le_journal_d_une_stack_n_est_pas_rejoue_par_l_autre(
    serveur: ServeurFactice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le chemin de l'incident : l'hôte détaché publie (pont, synchrone), l'API
    rejoue son journal au démarrage (asynchrone)."""
    dans(monkeypatch, "copie-a")
    publieur_redis()(statut_de_run("run-a"))
    journal_a = RedisEventLog()
    dans(monkeypatch, "copie-b")
    journal_b = RedisEventLog()
    dans(monkeypatch, COMMUN)
    journal_commun = RedisEventLog()

    assert [e.run_id for e in asyncio.run(journal_a.relire())] == ["run-a"]
    assert asyncio.run(journal_b.relire()) == []
    assert asyncio.run(journal_commun.relire()) == [], "le clone principal non plus"
    assert serveur.cles() == {f"copie-a:{CLE_JOURNAL_EVENEMENTS}"}


def test_le_direct_d_une_stack_n_atteint_pas_l_autre(
    serveur: ServeurFactice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le Pub/Sub, que les bases numérotées ne séparent pas : seul le nom sépare."""

    async def scene() -> tuple[list[str], list[str]]:
        dans(monkeypatch, "copie-a")
        bus_a = RedisEventBus()
        dans(monkeypatch, "copie-b")
        bus_b = RedisEventBus()
        flux_a, flux_b = bus_a.subscribe(), bus_b.subscribe()
        attente_a = asyncio.ensure_future(anext(flux_a))
        attente_b = asyncio.ensure_future(anext(flux_b))
        for _ in range(3):  # les deux abonnements sont posés
            await asyncio.sleep(0)

        dans(monkeypatch, "copie-a")
        publieur_redis()(statut_de_run("run-a"))
        recu_a = await asyncio.wait_for(attente_a, 1)
        await asyncio.sleep(0)
        assert not attente_b.done(), "la stack b a reçu un événement de la stack a"
        attente_b.cancel()
        await flux_a.aclose()
        return [recu_a.run_id or ""], []

    recus_a, recus_b = asyncio.run(scene())
    assert recus_a == ["run-a"]
    assert recus_b == []


def test_les_battements_d_une_stack_ne_font_pas_vivre_les_runs_de_l_autre(
    serveur: ServeurFactice, monkeypatch: pytest.MonkeyPatch
) -> None:
    dans(monkeypatch, "copie-a")
    batteur_redis()("run-a")
    registre_a = RegistreBattementsRedis()
    dans(monkeypatch, "copie-b")
    registre_b = RegistreBattementsRedis()

    assert set(asyncio.run(registre_a.battements())) == {"run-a"}
    assert asyncio.run(registre_b.battements()) == {}
    assert serveur.cles() == {f"copie-a:{CLE_BATTEMENTS}"}


def test_les_boites_aux_lettres_d_une_stack_ne_s_ouvrent_pas_sur_l_autre(
    serveur: ServeurFactice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le chat et les messages inter-agents : un agent `dev` dans chaque copie."""

    async def scene() -> tuple[int, int]:
        dans(monkeypatch, "copie-a")
        boites_a = RedisMailbox()
        dans(monkeypatch, "copie-b")
        boites_b = RedisMailbox()
        await boites_a.subscribe("dev")
        await boites_b.subscribe("dev")
        await boites_a.publish(
            AgentMessage(type=MESSAGE_NOTIFICATION, de_agent="qa", a_agent="dev", objet="salut")
        )
        par_canal: dict[str, int] = {}
        for abonnement in serveur.abonnements:
            for canal, _brut in abonnement.recus:
                par_canal[canal] = par_canal.get(canal, 0) + 1
        return par_canal.get("copie-a:maestro.boite.dev", 0), sum(
            n for c, n in par_canal.items() if c.startswith("copie-b:")
        )

    recus_a, recus_b = asyncio.run(scene())
    assert (recus_a, recus_b) == (1, 0)


def test_la_file_de_taches_est_celle_de_l_espace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un worker lancé depuis une copie ne prend que les tâches de cette copie."""
    dans(monkeypatch, "copie-a")
    assert celery_app.create_app().conf.task_default_queue == f"copie-a:{celery_app.FILE_TACHES}"
    dans(monkeypatch, COMMUN)
    assert celery_app.create_app().conf.task_default_queue == celery_app.FILE_TACHES


def test_la_purge_d_une_copie_ne_vide_que_ses_donnees(
    serveur: ServeurFactice, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`python -m maestro.controltower.purge` joué dans un worktree : les données
    du clone principal, rangées sous d'autres noms, restent entières."""
    from maestro.config import Settings
    from maestro.controltower import purge

    dans(monkeypatch, COMMUN)
    publieur_redis()(statut_de_run("run-du-poste"))
    dans(monkeypatch, "copie-b")
    publieur_redis()(statut_de_run("run-b"))

    for variable, sous in (
        ("MAESTRO_CHAT_DIR", "chat"),
        ("MAESTRO_INGESTION_DIR", "ingestion"),
        ("MAESTRO_PROJETS_DIR", "projets"),
    ):
        monkeypatch.setenv(variable, str(tmp_path / sous))
    settings = Settings.from_env()
    perimetre = purge.perimetre(settings)
    assert perimetre.espace.nom == "copie-b"
    assert perimetre.journal == f"copie-b:{CLE_JOURNAL_EVENEMENTS}"
    assert perimetre.battements == nom_redis(CLE_BATTEMENTS)

    code = purge.main(
        [],
        client=ClientSynchrone(serveur),
        sonde_api=lambda: False,
        settings=settings,
        sortie=open(tmp_path / "sortie.txt", "w", encoding="utf-8"),  # noqa: SIM115
        erreur=open(tmp_path / "erreur.txt", "w", encoding="utf-8"),  # noqa: SIM115
    )
    assert code == purge.CODE_FAIT
    assert serveur.cles() == {CLE_JOURNAL_EVENEMENTS}, "le journal du poste reste entier"
    assert "espace « copie-b »" in (tmp_path / "sortie.txt").read_text(encoding="utf-8")


# ── ③ La séparation, API comprise ─────────────────────────────────────────────


def api_reelle(monkeypatch: pytest.MonkeyPatch, espace: str, tmp_path: Path) -> Any:
    """Une API construite comme `create_default_app` : les objets Redis de production,
    dans l'espace de sa stack, et ses dépôts de fichiers dans sa propre copie."""
    dans(monkeypatch, espace)
    copie_racine = tmp_path / espace / "core"
    for variable, sous in DEPOTS:
        monkeypatch.setenv(variable, str(copie_racine / sous))
    return create_app(
        bus=RedisEventBus(),
        mailbox=RedisMailbox(),
        event_log=RedisEventLog(),
        battements=RegistreBattementsRedis(),
        state=ControlTowerState(),
    )


#: Les dépôts de fichiers d'une API, déplacés dans `tmp_path` : une API de test ne
#: lit ni n'écrit jamais le `core/` du dépôt qui joue la suite.
DEPOTS = (
    ("MAESTRO_CHAT_DIR", "chat"),
    ("MAESTRO_PROJETS_DIR", "projets"),
    ("MAESTRO_INGESTION_DIR", "ingestion"),
    ("MAESTRO_AGENTS_DIR", "agents"),
    ("MAESTRO_SURCHARGES_DIR", "surcharges"),
    ("MAESTRO_PLAYBOOKS_DIR", "playbooks"),
    ("MAESTRO_PERMISSIONS_DIR", "permissions"),
    ("MAESTRO_MCP_DIR", "mcp"),
    ("MAESTRO_CAPACITE_DIR", "capacite"),
    ("MAESTRO_SECRETS_DIR", "secrets"),
    ("MAESTRO_MCP_AMONT_DIR", "mcp-amont"),
)


def runs_vus(app: Any) -> set[str]:
    with TestClient(app) as client:
        reponse = client.get("/api/executions?projet=tous")
        assert reponse.status_code == 200, reponse.text
        return {execution["run_id"] for execution in reponse.json()}


def test_deux_api_reelles_ne_voient_que_les_runs_de_leur_copie(
    serveur: ServeurFactice, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Le critère, au plus près de la stack : un run publié par un hôte de la copie
    a, deux API réelles qui démarrent sur le même Redis — seule la sienne le voit."""
    dans(monkeypatch, "copie-a")
    publieur_redis()(statut_de_run("run-a"))
    dans(monkeypatch, "copie-b")
    publieur_redis()(statut_de_run("run-b"))

    assert runs_vus(api_reelle(monkeypatch, "copie-a", tmp_path)) == {"run-a"}
    assert runs_vus(api_reelle(monkeypatch, "copie-b", tmp_path)) == {"run-b"}
    assert runs_vus(api_reelle(monkeypatch, COMMUN, tmp_path)) == set()


# ── ④ Les fils et les projets suivent la même copie ─────────────────────────


def test_fils_et_projets_vivent_dans_la_copie_dont_l_espace_est_deduit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sans réglage, fils et projets vivent sous `core/` de la copie du code — la
    même racine que celle d'où l'espace se déduit. Deux copies, deux `core/`."""
    for variable in ("MAESTRO_CHAT_DIR", "MAESTRO_PROJETS_DIR"):
        monkeypatch.delenv(variable, raising=False)
    racine = espace_module.racine_de_la_copie()
    assert ChatStore.default().racine == racine / "core" / "chat"
    assert ProjetStore.default().racine == racine / "core" / "projets"
