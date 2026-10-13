"""Un passage de `/etat-des-lieux` : chaque question payée une fois, jamais une carte relue (#1490).

Mesuré le 2026-10-13 : la commande passait **~5 min sur ~6 à attendre ses lectures** — 309 s et 113
appels `gh` rejoués verbe par verbe, dont **60 pages de la même carte des Status**. La carte de tout
le projet (~1 000 items, 10 pages) recouvrait des tables bornées à 100 tickets, et sa mémoire
(#362) ne vivait que dans un processus, quand chaque verbe de la commande en est un. Les tables
demandent désormais l'état de LEURS tickets à `st_statuts`, en un aller : sur la vraie forge, sortie
identique ligne pour ligne et ~4 s au lieu de ~27 s par table (docs/10 §3.6).

Ce module garde ce qui ne se voit pas au chronomètre, par un COMPTE d'appels sur le harnais de forge
(#602) :

- un passage des lectures de forge de la commande — **lues dans son fichier, jamais listées ici** —
  ne pagine jamais le projet, et paie une lecture d'état par table, ni une par page ni une par
  ticket ; les tables y rendent les états attendus ;
- `doctor.sh` lit l'état des PR de N branches locales en UN appel, et nomme les mêmes branches.

Le troisième poste de #1490 — `status.sh --list` qui lisait la date de chaque fichier de journal —
se garde dans `tests/test_orchestrate.py`, à côté des autres tests de `status.sh`.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    GIT,
    MOI,
    PROJET,
    RACINE,
    Depot,
    colonnes,
    lignes_projet,
    monte_depot,
    noeud_ticket,
    regle_statuts,
    regles_backlog,
    reponse_backlog,
)

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

COMMANDE = RACINE / ".claude/commands/etat-des-lieux.md"

#: Une page d'items du projet, et la résolution du projet par son titre qui la précédait : les
#: deux allers de la carte. Leur présence au journal est une carte relue.
PAGE_DE_CARTE = "items(first:100"
RESOLUTION_DU_PROJET = "projectsV2(first:100){nodes{ id title }}"

#: Le fragment de `st_statuts`, seul à demander `nodes{ project{title}` (cf. `regle_statuts`).
LECTURE_D_ETAT = "nodes{ project{title}"

#: Les deux lectures qui LISTENT une table : le backlog (ouvert, fermé ou les deux) et les tickets
#: d'un jalon. Chacune appelle exactement une lecture d'état.
LECTURES_DE_TABLE = (
    "states: [OPEN]", "states: [CLOSED]", "states: [OPEN, CLOSED]", "milestone(number:",
)

JALON_PRODUIT = "Phase P"
JALON_OUTILLAGE = "Outillage O"

#: L'état de chaque ticket du décor : 11, 12 au jalon produit, 21, 22 au jalon outillage, tous
#: ouverts ; 5 et 6 fermés.
ETATS = {"11": "À faire", "12": "En cours", "21": "À faire", "22": "En revue"}
ETATS_FERMES = {"5": "Terminé", "6": "Abandonné"}


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


def verbes_de_forge() -> list[list[str]]:
    """Les lectures de forge que la commande fait jouer, lues dans ses blocs de code.

    Ni listées ni recopiées : un verbe ajouté à la commande entre ici de lui-même, et c'est ce qui
    fait de ce module une garde de la COMMANDE et non d'une liste qui la suivrait de loin. Ne sont
    retenues que les lignes `bash scripts/gitlab/…` : `status.sh` et `journal.sh` (étape 4) lisent
    le disque, pas la forge.
    """
    texte = COMMANDE.read_text(encoding="utf-8")
    verbes = []
    for bloc in re.findall(r"```\n(.*?)```", texte, re.S):
        for ligne in bloc.splitlines():
            ligne = ligne.strip()
            if ligne.startswith("bash scripts/gitlab/"):
                verbes.append(shlex.split(ligne)[1:])
    return verbes


def _jalon(numero: int, titre: str, description: str, tickets: list[str]) -> dict:
    return {
        "number": numero,
        "title": titre,
        "description": description,
        "state": "OPEN",
        "dueOn": f"2026-12-0{numero}T00:00:00Z",
        "total": {"totalCount": len(tickets)},
        "fermes": {"totalCount": 0},
    }


def regles_d_un_passage() -> list[dict]:
    """La forge d'un passage : deux jalons actifs (un par rail), leurs tickets, le backlog des deux
    côtés, et de quoi répondre à `doctor.sh`. Les lectures qu'aucune règle ne sert échouent : le
    verbe le dit, et le compte d'appels n'en dépend pas."""
    jalons = [
        (1, JALON_PRODUIT, "", ["11", "12"]),
        (2, JALON_OUTILLAGE, "rail: outillage", ["21", "22"]),
    ]
    ticket = {"number": 0, "title": "", "labels": {"nodes": [{"name": "type::infra"}]}}
    derives = [f"total\t{len(ETATS)}"] + [f"ticket\t{iid}" for iid in ETATS] + [
        f"item\t{iid}\t{PROJET}\t{etat}" for iid, etat in ETATS.items()
    ]
    return [
        # Une seule règle d'état pour tous les tickets, ouverts comme fermés : la lecture ne rend
        # que ce qu'on lui demande en production, le double rend tout, et la table ne recouvre
        # que ses lignes. Le backlog OUVERT, lui, ne liste que les ouverts.
        regle_statuts({**ETATS, **ETATS_FERMES}),
        *regles_backlog(ETATS)[1:],
        {
            "contient": ["states: [CLOSED]"],
            "reponse": reponse_backlog(
                [noeud_ticket(iid, f"Ticket {iid}", "", ["type::infra"], [MOI])
                 for iid in ETATS_FERMES]
            ),
        },
        {
            # `current-milestone` : les jalons OUVERTS, titre d'abord (ordre de la requête).
            "contient": ["milestones(first: 20, states: OPEN"],
            "reponse": {"data": {"repository": {"milestones": {"nodes": [
                {"title": t, "description": d, "total": {"totalCount": len(ts)},
                 "fermes": {"totalCount": 0}}
                for _, t, d, ts in jalons
            ]}}}},
        },
        {
            "contient": ["milestones(first: 50, orderBy"],
            "reponse": {"data": {"repository": {"milestones": {"nodes": [
                {k: v for k, v in _jalon(n, t, d, ts).items() if k != "number"}
                for n, t, d, ts in jalons
            ]}}}},
        },
        {
            "contient": ["nodes { number title }"],
            "reponse": {"data": {"repository": {"milestones": {"nodes": [
                {"number": n, "title": t} for n, t, _, _ in jalons
            ]}}}},
        },
        *[
            {
                "contient": [f"milestone(number: {n})"],
                "reponse": {"data": {"repository": {"milestone": {"issues": {"nodes": [
                    {**ticket, "number": int(iid), "title": f"Ticket {iid}"} for iid in ts
                ]}}}}},
            }
            for n, _, _, ts in jalons
        ],
        {
            "contient": ["pullRequests(states: OPEN"],
            "reponse": {"data": {"repository": {"pullRequests": {"nodes": []}}}},
        },
        {"contient": ["options{id name}"], "brut": "\n".join(lignes_projet()) + "\n"},
        {"contient": ["issues(states: OPEN, first: 100)"], "brut": "\n".join(derives) + "\n"},
    ]


def test_la_commande_lit_ses_tables_par_les_verbes_que_ce_module_compte() -> None:
    """Sans ces verbes dans la commande, le passage compté plus bas ne prouverait rien."""
    noms = [" ".join(v[:2]) for v in verbes_de_forge()]
    for attendu in (
        "scripts/gitlab/lib.sh backlog-table",
        "scripts/gitlab/lib.sh reconcile-en-cours",
        "scripts/gitlab/lib.sh milestone-issues",
        "scripts/gitlab/doctor.sh",
    ):
        assert any(nom.startswith(attendu) for nom in noms), f"{attendu} absent de : {noms}"


def test_un_passage_ne_relit_jamais_la_carte_et_paie_un_etat_par_table(depot: Depot) -> None:
    """LE test de #1490 : un passage de la commande, compté sur le harnais (#602).

    Avant : 6 cartes de 10 pages pour une commande. Après : AUCUNE page — chaque table demande
    l'état de ses propres tickets en un aller, donc autant de lectures d'état que de tables lues,
    et chacune ne nomme que des tickets que la forge vient de lister. Les tables rendent les états
    du décor : le contenu ne bouge pas.
    """
    depot.pose_etat(graphql=regles_d_un_passage())
    lanceurs = {"scripts/gitlab/lib.sh": depot.lib, "scripts/gitlab/doctor.sh": depot.doctor}
    sorties: dict[str, str] = {}
    for verbe in verbes_de_forge():
        script, *args = verbe
        assert script in lanceurs, f"la commande lit la forge par un script inconnu ici : {verbe}"
        if args and args[-1] == "<titre>":
            for titre in (JALON_PRODUIT, JALON_OUTILLAGE):
                acheve = lanceurs[script](*args[:-1], titre)
                assert acheve.returncode == 0, (verbe, titre, acheve.stderr)
                sorties[titre] = acheve.stdout
            continue
        sorties[" ".join(verbe)] = lanceurs[script](*args).stdout

    appels = depot.appels()
    assert not [a for a in appels if PAGE_DE_CARTE in a], "une page de la carte du projet"
    assert not [a for a in appels if RESOLUTION_DU_PROJET in a], "le projet résolu pour sa carte"

    tables = [a for a in appels if any(f in a for f in LECTURES_DE_TABLE)]
    etats = [a for a in appels if LECTURE_D_ETAT in a]
    assert tables, "aucune table lue : le passage ne prouverait rien"
    assert len(etats) == len(tables), f"{len(etats)} lectures d'état pour {len(tables)} tables"
    connus = set(ETATS) | set(ETATS_FERMES)
    for lecture in etats:
        assert set(re.findall(r"issue\(number:(\d+)\)", lecture)) <= connus, lecture

    # Le contenu : chaque table porte l'état de chacun de ses tickets.
    backlog = {
        ligne[0]: ligne[1]
        for ligne in colonnes(sorties["scripts/gitlab/lib.sh backlog-table"])
    }
    assert backlog == ETATS
    for titre, tickets in ((JALON_PRODUIT, ("11", "12")), (JALON_OUTILLAGE, ("21", "22"))):
        assert {ligne[0]: ligne[1] for ligne in colonnes(sorties[titre])} == {
            iid: ETATS[iid] for iid in tickets
        }


def _section(texte: str, debut: str, fin: str) -> str:
    return texte.split(debut, 1)[1].split(fin, 1)[0]


def test_doctor_lit_l_etat_des_pr_de_n_branches_en_un_appel(depot: Depot) -> None:
    """`doctor.sh` §5 payait une lecture de PR par branche locale — 16 allers un par un (#1490).

    Une lecture pour toutes, par le verbe même sur lequel la purge décide (`mr-briefs`, #602) : les
    mêmes verdicts — la branche mergée est nommée, celle dont la PR est ouverte et celle qui n'en a
    pas ne le sont pas. Le RANG fait le lien entre la question et la réponse (alias `b0:`, `b1:`…),
    dans l'ordre où `git branch` rend les branches.
    """
    branches = ("chore/401-a", "chore/402-b", "chore/403-c")
    for branche in branches:
        depot.git("branch", branche)
    depot.pose_etat(graphql=[{
        "contient": ["pullRequests(headRefName:"],
        "reponse": {"data": {"repository": {
            "b0": {"nodes": [{"number": 41, "state": "MERGED", "headRefOid": "a" * 40}]},
            "b1": {"nodes": [{"number": 42, "state": "OPEN", "headRefOid": "b" * 40}]},
            "b2": {"nodes": []},
        }}},
    }])

    acheve = depot.doctor()
    bilan = _section(acheve.stdout, "5. Ménage des branches locales", "\n6. ")

    lectures = [a for a in depot.appels() if "pullRequests(headRefName:" in a]
    assert len(lectures) == 1, f"{len(lectures)} lectures de PR pour {len(branches)} branches"
    assert all(f'"{b}"' in lectures[0] for b in branches), lectures[0]
    assert "« chore/401-a » : PR mergée" in bilan, bilan
    assert "chore/402-b" not in bilan and "chore/403-c" not in bilan, bilan
