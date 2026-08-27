"""Tests du chantier « travail à plusieurs sur des clones distincts » (ticket #156, parent #155).

Lot final : les lots #157 à #164 ont délibérément différé leurs tests ici (docs/10 §5.1). Ce
module couvre ce qu'ils ont ajouté à [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) et à
[`scripts/gitlab/doctor.sh`](../scripts/gitlab/doctor.sh) :

* **anti-collision** (#159) — `issue-owner` / `issue-taken` / `start-brief` : savoir qu'un ticket
  est déjà pris **avant** de le démarrer, `begin` REMPLAÇANT la liste des assignés ;
* **lots parallélisables** (#160) — `subtickets` / `startables` : le marqueur « (parallèle) » et la
  règle de blocage entre lots d'un parent ;
* **revue best-effort** (#161, révisé par #196) — `project-humans` / `pick-reviewer` /
  `set-reviewer` / `review-queue` : un relecteur humain ≠ auteur, jamais remplacé, et la file la
  plus ancienne d'abord. Depuis #196 la pose n'est plus **automatique** : le helper reste outillé
  pour un appel manuel, mais aucune commande du workflow ne l'invoque ;
* **retard sur `origin/main`** (#163) — `behind-main` : le constat et l'heuristique de conflit ;
* **garde-fou de clôture** (#164) — `branch-iid` / `close-guard` : la session traite-t-elle bien
  ce ticket ;
* **contrôle doctor du runner** (#157) — section 7 de `doctor.sh`.

S'y ajoutent, parce que c'est le module qui outille `lib.sh` face à un `gh` factice :

* la **création depuis un fichier** (#233, parent #232) — `create-mr` / `issue-note` /
  `issue-title` : le texte long voyage par FICHIER pour qu'aucune commande d'une session autonome
  ne porte de saut de ligne ni de `$(…)`, formes qu'aucune règle de permission ne peut reconnaître
  (docs/10 §11.7) ;
* la **jointure de temps** (#400, docs/27 §12.4) — `get-time-spent` / `get-start-date` /
  `log-time` : l'historique importé de GitLab (`maestro:meta v1`) et le suivi quotidien
  (`maestro:suivi:v1`) sont deux formats, et c'est la LECTURE qui les joint, le commentaire
  d'import n'étant jamais réécrit.

**Le harnais a été sorti d'ici** (#366) : le dépôt jetable, le `gh` factice et les fabriques de
réponses vivent dans [`harnais_forge.py`](harnais_forge.py), d'où ce module les importe. Rien n'y a
changé de comportement — le déplacement a été fait pour que
[`test_cycle_de_vie.py`](test_cycle_de_vie.py) s'en serve sans le recopier, deux `gh` factices à
tenir d'accord étant la meilleure façon de rendre une suite verte sur une forme de réponse que
l'autre a corrigée depuis. Le seul contrat qui reste ici est celui de sa docstring : les tests
décrivent leurs tickets dans le format de SORTIE (`corps_ticket`), le double se chargeant de la
traduction.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    ECRITURES,
    GIT,
    MOI,
    RACINE,
    Depot,
    colonnes,
    corps_ticket,
    ecritures,
    monte_depot,
    regle_owner,
    regle_pose_status,
    regle_statuts,
    regles_backlog,
)

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


# =================================================================================================
# Anti-collision : à qui appartient le ticket ? (#159)
# =================================================================================================


def test_issue_owner_rend_statut_et_assignes(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("En cours", ["bea"])]
    )
    acheve = depot.lib("issue-owner", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.strip() == "En cours\tbea"


def test_issue_owner_rend_un_champ_vide_pour_un_ticket_libre(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("À faire", [])]
    )
    acheve = depot.lib("issue-owner", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.rstrip("\n") == "À faire\t"


def test_issue_owner_refuse_un_ticket_introuvable(depot: Depot) -> None:
    """Sans ce garde-fou, deux champs vides passeraient pour « statut non posé, ticket libre ».

    Les deux causes sont NOMMÉES par le programme jq de `st_contexte` (« erreur<TAB>ticket » /
    « erreur<TAB>depot ») et non déduites d'une réponse vide — c'est ce qui les distingue d'un
    ticket réellement sans état, qui est un résultat légitime.
    """
    depot.pose_etat(
        graphql=[{"contient": ["projectItems(first:"], "brut": "erreur\tticket\n"}]
    )
    acheve = depot.lib("issue-owner", "999")
    assert acheve.returncode == 1
    assert "introuvable" in acheve.stderr
    assert acheve.stdout.strip() == ""


def test_issue_owner_refuse_un_projet_illisible(depot: Depot) -> None:
    """Dépôt inconnu ou droits insuffisants : GraphQL sort en code 0 avec un `repository` nul."""
    depot.pose_etat(
        graphql=[{"contient": ["projectItems(first:"], "brut": "erreur\tdepot\n"}]
    )
    acheve = depot.lib("issue-owner", "159")
    assert acheve.returncode == 1
    assert "illisible" in acheve.stderr


def test_issue_taken_signale_un_ticket_en_cours_chez_un_autre(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("En cours", ["bea"])]
    )
    acheve = depot.lib("issue-taken", "159")
    assert acheve.returncode == 0
    assert acheve.stdout.strip() == "bea"


def test_issue_taken_ne_signale_pas_mon_propre_ticket(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("En cours", [MOI])]
    )
    assert depot.lib("issue-taken", "159").returncode == 1


def test_issue_taken_ne_signale_ni_un_ticket_libre_ni_un_autre_statut(depot: Depot) -> None:
    """Prédicat volontairement étroit : seul « En cours » chez un tiers est une collision."""
    depot.pose_etat(
        graphql=[regle_owner("En cours", [])]
    )
    assert depot.lib("issue-taken", "159").returncode == 1

    depot.pose_etat(
        graphql=[regle_owner("En revue", ["bea"])]
    )
    assert depot.lib("issue-taken", "159").returncode == 1


def test_issue_taken_ne_confond_pas_un_username_avec_son_prefixe(depot: Depot) -> None:
    """« bea » ne doit pas se reconnaître dans « bea-bot » : l'appartenance est EXACTE."""
    depot.pose_etat(
        moi="bea",
        graphql=[
            regle_owner("En cours", ["bea-bot"])
        ],
    )
    acheve = depot.lib("issue-taken", "159")
    assert acheve.returncode == 0, "un ticket de bea-bot n'appartient pas à bea"
    assert acheve.stdout.strip() == "bea-bot"


# =================================================================================================
# Préflight de /ticket-start (#159)
# =================================================================================================

TICKET_SIMPLE = corps_ticket(
    "Anti-collision sur les tickets",
    "agent::devops, prio::moyenne, type::infra",
    "## Contexte\n\nDeux sessions, un seul ticket.\n\n"
    "## Critères d'acceptation\n\n- [ ] Le ticket pris est signalé\n",
)


def test_start_brief_signale_un_arbre_non_propre_sans_bloquer(depot: Depot) -> None:
    """Depuis #181, l'arbre sale est un AVERTISSEMENT et non plus un refus.

    L'intention d'origine — ne pas démarrer par-dessus le travail d'une autre session — n'est pas
    abandonnée, elle change de porteur : `/ticket-start` monte désormais un worktree par ticket, si
    bien que des changements non commités ici restent derrière nous, intacts et hors du chemin.
    Les refuser bloquerait le démarrage pour une saleté sans rapport avec le ticket visé. La
    décision revient à la commande, seule à connaître le verdict de `worktree.sh ensure` :
    bloquante sur « ICI » (on travaillerait dans cet arbre), anodine sur « WORKTREE ».
    """
    depot.pose_etat(issues={"159": TICKET_SIMPLE})
    (depot.racine / "fichier-a.txt").write_text("modifié\n", encoding="utf-8", newline="\n")

    acheve = depot.lib("start-brief", "159")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "arbre de travail non propre" in acheve.stderr
    assert "1 fichier(s) non commité(s)" in acheve.stderr, "le compte rend l'alerte actionnable"
    # Le brief est bien rendu : c'est tout l'intérêt de ne plus sortir avant de l'imprimer.
    assert "#159" in acheve.stdout


def test_start_brief_annonce_un_ticket_libre(depot: Depot) -> None:
    depot.pose_etat(
        issues={"159": TICKET_SIMPLE},
        graphql=[regle_owner("À faire", [])],
    )
    acheve = depot.lib("start-brief", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert "statut : À faire — libre (aucun assigné)" in acheve.stdout
    assert "⚠" not in acheve.stdout
    assert "branche proposée : chore/159-anti-collision-sur-les-tickets" in acheve.stdout
    # Le brief est une PROJECTION : critères oui, contexte non (moins de contexte réinjecté).
    assert "Le ticket pris est signalé" in acheve.stdout
    assert "Deux sessions, un seul ticket." not in acheve.stdout


def test_start_brief_avertit_sur_un_ticket_deja_pris(depot: Depot) -> None:
    """`begin` REMPLACE les assignés : démarrer un ticket pris le retirerait en silence."""
    depot.pose_etat(
        issues={"159": TICKET_SIMPLE},
        graphql=[
            regle_owner("En cours", ["bea"])
        ],
    )
    acheve = depot.lib("start-brief", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert "⚠ déjà pris par bea" in acheve.stdout
    assert "retirerait son assignation" in acheve.stdout


def test_start_brief_ne_s_alarme_pas_de_mon_propre_ticket(depot: Depot) -> None:
    depot.pose_etat(
        issues={"159": TICKET_SIMPLE},
        graphql=[
            regle_owner("En cours", [MOI])
        ],
    )
    acheve = depot.lib("start-brief", "159")
    assert f"statut : En cours — pris par : {MOI}" in acheve.stdout
    assert "déjà pris" not in acheve.stdout


def test_start_brief_dit_quand_l_appartenance_est_illisible(depot: Depot) -> None:
    """Une lecture en échec ne doit pas se lire comme un feu vert."""
    depot.pose_etat(issues={"159": TICKET_SIMPLE}, graphql=[])
    acheve = depot.lib("start-brief", "159")
    assert acheve.returncode == 0, acheve.stderr
    assert "appartenance illisible" in acheve.stdout


def test_start_brief_signale_une_branche_sans_prefixe(depot: Depot) -> None:
    """Sans label `type::`, on ne fabrique pas une branche mal nommée : on le dit."""
    depot.pose_etat(
        issues={"159": corps_ticket("Sans type", "agent::dev, prio::basse", "Corps.")},
        graphql=[regle_owner("À faire", [])],
    )
    acheve = depot.lib("start-brief", "159")
    assert "<type>/159-sans-type (label type:: absent" in acheve.stdout


# =================================================================================================
# Lots parallélisables d'un parent de suivi (#160)
# =================================================================================================

PARENT = corps_ticket(
    "Travail à plusieurs",
    "agent::devops, prio::moyenne, type::infra",
    """## Sous-tickets

- [x] #201 — Socle du chantier
- [ ] #202 — Écran de suivi (parallèle)
- [ ] #203 — Endpoint de lecture (parallèle)
- [ ] #204 — Tests + doc

## Notes

Rien à voir avec la checklist.
""",
)


def backlog_des_lots(statuts: dict[str, str]) -> list[dict]:
    """L'état des lots d'un parent, tel que `gl_subtickets_enrich` le demande.

    ⚠ UNE SEULE RÈGLE, ET C'EST LE SUJET DE #577. L'enrichissement demandait autrefois le backlog
    ENTIER (« qui existe ? ») recouvert par la carte d'ensemble du projet (« quel état ? »), soit
    six allers pour une question qui en vaut un ; il demande désormais les lots PAR LEUR NUMÉRO
    (`st_statuts`). Ne pas remettre ici les règles de la carte et du backlog « au cas où » : leur
    absence est ce qui prouve que le verbe ne les demande plus — une règle de trop rendrait ce
    test vert sur l'ancien chemin comme sur le nouveau.
    """
    return [regle_statuts(statuts)]


def test_subtickets_lit_la_checklist_et_le_marqueur_parallele(depot: Depot) -> None:
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots(
            {"201": "Terminé", "202": "À faire", "203": "À faire", "204": "À faire"}
        ),
    )
    acheve = depot.lib("subtickets", "155")
    assert acheve.returncode == 0, acheve.stderr
    lignes = colonnes(acheve.stdout)
    assert [ligne[0] for ligne in lignes] == ["201", "202", "203", "204"]
    assert [ligne[1] for ligne in lignes] == ["x", "-", "-", "-"]      # coche de la checklist
    assert [ligne[2] for ligne in lignes] == ["Terminé", "À faire", "À faire", "À faire"]
    assert [ligne[3] for ligne in lignes] == ["-", "∥", "∥", "-"]      # marqueur « (parallèle) »
    # Le marqueur est extrait dans sa colonne, pas laissé dans le titre.
    assert lignes[1][4] == "Écran de suivi"
    # Et la section suivante du corps n'a pas débordé dans la checklist.
    assert "Rien à voir" not in acheve.stdout


def test_subtickets_refuse_un_ticket_sans_checklist(depot: Depot) -> None:
    depot.pose_etat(issues={"159": TICKET_SIMPLE})
    acheve = depot.lib("subtickets", "159")
    assert acheve.returncode == 1
    assert "pas un ticket parent" in acheve.stderr


def test_startables_laisse_les_lots_paralleles_se_prendre_ensemble(depot: Depot) -> None:
    """Le cœur de #160 : deux lots marqués ne se bloquent pas — deux personnes les prennent."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots(
            {"201": "Terminé", "202": "À faire", "203": "À faire", "204": "À faire"}
        ),
    )
    acheve = depot.lib("startables", "155")
    assert acheve.returncode == 0, acheve.stderr
    assert "#202" in acheve.stdout and "(parallèle)" in acheve.stdout
    assert "#203" in acheve.stdout
    # …mais le lot final « tests + doc », non marqué, reste derrière tout le monde.
    assert "#204" not in acheve.stdout


def test_startables_ne_bloque_pas_sur_un_lot_precedent_en_revue(depot: Depot) -> None:
    """Les lots s'enchaînent dès « En revue » : une PR en attente de merge ne barre rien (#63)."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots(
            {"201": "En revue", "202": "Terminé", "203": "En revue", "204": "À faire"}
        ),
    )
    acheve = depot.lib("startables", "155")
    assert "#204" in acheve.stdout


def test_startables_barre_un_lot_parallele_derriere_un_lot_non_marque(depot: Depot) -> None:
    """Le marqueur n'affranchit que des AUTRES lots marqués, pas d'un lot ordinaire en retard."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots(
            {"201": "En cours", "202": "À faire", "203": "À faire", "204": "À faire"}
        ),
    )
    acheve = depot.lib("startables", "155")
    assert acheve.stdout.strip() == ""


# --- Le prix de la question (#577) ----------------------------------------------------------------
# `subtickets` et `startables` sont les deux verbes les plus appelés d'un run (16 invocations sur 31
# au run 20260826-134119), et ils payaient l'état de leurs lots au prix de l'ENSEMBLE : le backlog
# entier, recouvert par la carte du projet paginée en pages de 100 items — six allers pour une
# question qui en vaut un, ~30 s pièce. Ils demandent désormais les lots PAR LEUR NUMÉRO.
#
# Ce que ces deux tests gardent n'est pas une durée (un chronomètre en CI mesure la charge de la
# machine) mais **ce qui est demandé à la forge** : c'est la seule forme sous laquelle la propriété
# survit à un poste lent, et c'est aussi elle qui porte la CORRECTION — voir le second test.


def test_subtickets_demande_les_lots_par_leur_numero_et_pas_le_backlog(depot: Depot) -> None:
    """Deux lectures en tout : la checklist du parent, puis l'état des lots nommés."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=backlog_des_lots({"201": "Terminé", "202": "À faire", "203": "À faire"}),
    )
    acheve = depot.lib("subtickets", "155")
    assert acheve.returncode == 0, acheve.stderr

    lectures = [appel for appel in depot.appels() if "graphql" in appel]
    assert len(lectures) == 2, lectures
    # Ni le backlog (« qui existe ? ») ni la carte d'ensemble (« quel état pour tout le monde ? ») :
    # ce sont les deux lectures que la question ne demandait pas.
    assert not any("states: [OPEN, CLOSED]" in appel for appel in lectures), lectures
    assert not any("items(first:100" in appel for appel in lectures), lectures
    # …et les lots sont bien demandés sous leur alias, en UNE requête.
    par_numero = [appel for appel in lectures if "i201:" in appel]
    assert len(par_numero) == 1, lectures
    assert "i202:" in par_numero[0] and "i203:" in par_numero[0]


def test_subtickets_ne_depend_plus_de_la_fenetre_de_cent_du_backlog(depot: Depot) -> None:
    """La moitié qui ne se voit pas au chronomètre : le backlog est borné à `first: 100`.

    Un lot plus ancien que cette fenêtre en sortait absent, donc « ? », donc jamais « À faire »,
    donc JAMAIS DÉMARRABLE — un `/ticket-start` sur un tel parent annonçait « aucun lot démarrable »
    en croyant dire la vérité (mesuré le 2026-08-26 sur #167 : cinq lots « Terminé » rendus « ? »).

    Le double le rejoue à l'endroit exact où ça se jouait : AUCUNE règle de backlog n'est posée —
    c'est l'échantillon fautif —, seuls les lots nommés répondent. Sur l'ancien chemin la table
    sortait vide et les quatre lots « ? » ; ici ils portent leur état, et les lots libres sont
    démarrables.
    """
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=[
            regle_statuts(
                {"201": "Terminé", "202": "À faire", "203": "À faire", "204": "À faire"}
            )
        ],
    )
    table = depot.lib("subtickets", "155")
    assert table.returncode == 0, table.stderr
    assert [ligne[2] for ligne in colonnes(table.stdout)] == [
        "Terminé",
        "À faire",
        "À faire",
        "À faire",
    ]
    assert "?" not in table.stdout

    demarrables = depot.lib("startables", "155")
    assert demarrables.returncode == 0, demarrables.stderr
    assert "#202" in demarrables.stdout


def test_statuts_distingue_le_hors_projet_du_ticket_inexistant(depot: Depot) -> None:
    """« - » = le ticket existe mais n'a pas d'état ; aucune ligne = il n'existe pas.

    C'est le contrat de `gh_issues_state`, et la raison en est la même : ce que vaut le silence
    d'un alias appartient à l'appelant. `gl_subtickets_enrich` le compte « ? » — un iid de
    checklist qui ne désigne aucun ticket n'est pas un état, c'est une checklist à corriger.

    ⚠ Le double rend ici la réponse du PIRE cas — les alias inexistants s'accompagnant d'un
    tableau `errors` —, et c'est ce qui donne son sens au test : sur cette réponse-là,
    `gh api graphql --jq` recrache le JSON non filtré, si bien qu'un `st_statuts` écrit avec un
    `--jq` aurait rendu ZÉRO ligne, avec le code de succès. Les lots voisins auraient tous été
    « ? » à cause d'un seul numéro faux. Ce qui est gardé ici n'est pas le « - » : c'est que le
    reste de la réponse survive à l'erreur.
    """
    depot.pose_etat(
        graphql=[regle_statuts({"201": "Terminé", "202": ""}, inexistants=("999",))]
    )
    lu = depot.lib("statuts", "201", "202", "999")
    assert lu.returncode == 0, lu.stderr
    assert colonnes(lu.stdout) == [["201", "Terminé"], ["202", "-"]]

    depot.pose_etat(
        issues={"155": PARENT},
        graphql=[
            regle_statuts({"201": "Terminé", "202": ""}, inexistants=("203", "204"))
        ],
    )
    table = depot.lib("subtickets", "155")
    assert [ligne[2] for ligne in colonnes(table.stdout)] == ["Terminé", "-", "?", "?"]


def test_statuts_refuse_un_iid_qui_n_en_est_pas_un(depot: Depot) -> None:
    """Les iid partent dans un ALIAS GraphQL (`i201:`) : un argument non numérique s'y glisserait.

    Le refus a lieu AVANT toute lecture — la requête n'est pas construite, donc rien n'est envoyé.
    """
    acheve = depot.lib("statuts", "201; drop")
    assert acheve.returncode == 2
    assert "iid invalide" in acheve.stderr
    assert depot.appels() == []


def test_start_brief_sur_un_parent_liste_les_lots_demarrables(depot: Depot) -> None:
    """Un parent ne porte ni branche ni code : le brief redirige, il ne propose pas de branche."""
    depot.pose_etat(
        issues={"155": PARENT},
        graphql=[
            regle_owner("En cours", []),
            *backlog_des_lots(
                {"201": "Terminé", "202": "À faire", "203": "À faire", "204": "À faire"}
            ),
        ],
    )
    acheve = depot.lib("start-brief", "155")
    assert acheve.returncode == 0, acheve.stderr
    assert "parent de suivi — ne porte ni branche ni code" in acheve.stdout
    assert "lots démarrables maintenant" in acheve.stdout
    assert "#202" in acheve.stdout and "#203" in acheve.stdout
    assert "branche proposée" not in acheve.stdout


def test_start_brief_sur_un_sous_ticket_controle_les_lots_precedents(depot: Depot) -> None:
    sous_ticket = corps_ticket(
        "Endpoint de lecture",
        "agent::dev, prio::moyenne, type::infra",
        "Sous-ticket de #155 — lot 3/4.\n\nTests différés → #204.\n",
    )
    depot.pose_etat(
        issues={"155": PARENT, "203": sous_ticket},
        graphql=[
            regle_owner("À faire", []),
            *backlog_des_lots(
                {"201": "En cours", "202": "À faire", "203": "À faire", "204": "À faire"}
            ),
        ],
    )
    acheve = depot.lib("start-brief", "203")
    assert acheve.returncode == 0, acheve.stderr
    assert "sous-ticket de #155 — lot 3/4" in acheve.stdout
    assert "lot marqué « parallèle »" in acheve.stdout
    assert "⚠ non livrés : #201 (En cours)" in acheve.stdout
    # #202 est marqué « parallèle » comme #203 : il ne compte pas comme bloquant.
    assert "#202" not in acheve.stdout.split("lots précédents :")[1].splitlines()[0]
    # Les tests différés sont annoncés — livrer sans tests est prévu, pas un oubli.
    assert "tests différés → #204" in acheve.stdout


# =================================================================================================
# Revue best-effort : file de revue et relecteur posé à la main (#161, révisé par #196)
# =================================================================================================


def reponse_membres(membres: list[tuple[str, int, bool, str]]) -> dict:
    return {
        "data": {
            "project": {
                "collaborators": {
                    "edges": [
                        {
                            "permission": PERMISSION[niveau],
                            "node": {"login": nom, "__typename": "Bot" if bot else "User"},
                        }
                        for nom, niveau, bot, etat in membres
                        if etat == "active"
                    ]
                }
            }
        }
    }


#: L'échelle d'accès reste celle de GitLab (10/20/30/40/50) — c'est elle que porte
#: GL_REVIEWER_MIN_ACCESS et que compare `gl_pick_reviewer`, et `gh_project_humans` y traduit les
#: permissions GitHub. Le double fait donc la traduction inverse, pour que les tests continuent de
#: décrire un niveau plutôt qu'un mot de vocabulaire d'API.
PERMISSION = {10: "READ", 20: "TRIAGE", 30: "WRITE", 40: "MAINTAIN", 50: "ADMIN"}

#: Le dernier champ était l'état du compte (« blocked » côté GitLab). GitHub ne rend pas de compte
#: bloqué dans ses collaborateurs — un compte suspendu en sort tout court —, donc le double
#: l'écarte à la source plutôt que de simuler un champ qui n'existe pas.
MEMBRES = [
    ("bea", 40, False, "active"),
    ("cam", 30, False, "active"),
    ("dan", 30, False, "active"),
    (MOI, 40, False, "active"),        # compte d'automatisation : jamais relecteur
    ("invite", 20, False, "active"),   # sous le niveau Developer : ne peut ni pousser ni merger
    ("robot", 40, True, "active"),     # vrai bot, écarté par son __typename
    ("parti", 40, False, "blocked"),
]


def test_project_humans_ecarte_bots_niveaux_faibles_et_comptes_inactifs(depot: Depot) -> None:
    """Quatre exclusions, dont une que l'API seule ne saurait faire.

    Le compte de l'agent Maestro n'est pas un bot au sens de la forge (son `__typename` est
    `User`) : seule la configuration `GL_BOT_USERS` l'écarte. Sans elle, l'outillage se
    désignerait lui-même relecteur.
    """
    depot.pose_etat(graphql=[{"contient": ["collaborators("], "reponse": reponse_membres(MEMBRES)}])
    acheve = depot.lib("project-humans")
    assert acheve.returncode == 0, acheve.stderr
    retenus = {ligne[0] for ligne in colonnes(acheve.stdout)}
    assert retenus == {"bea", "cam", "dan"}


def test_pick_reviewer_ecarte_l_auteur_et_le_compte_d_automatisation(depot: Depot) -> None:
    depot.pose_etat(graphql=[{"contient": ["collaborators("], "reponse": reponse_membres(MEMBRES)}])
    for graine in range(6):
        acheve = depot.lib("pick-reviewer", "bea", str(graine))
        assert acheve.returncode == 0, acheve.stderr
        assert acheve.stdout.strip() in {"cam", "dan"}, acheve.stdout


def test_pick_reviewer_est_reproductible_mais_tourne(depot: Depot) -> None:
    """Même PR → même relecteur (pose idempotente) ; PR différentes → charge répartie."""
    depot.pose_etat(graphql=[{"contient": ["collaborators("], "reponse": reponse_membres(MEMBRES)}])
    choisis = [depot.lib("pick-reviewer", "bea", str(g)).stdout.strip() for g in range(4)]
    assert choisis[0] == depot.lib("pick-reviewer", "bea", "0").stdout.strip()
    assert len(set(choisis)) > 1, f"aucune rotation : {choisis}"


def test_pick_reviewer_echoue_proprement_sur_un_projet_d_une_personne(depot: Depot) -> None:
    """La revue est best-effort : l'appelant poursuit sans relecteur, sans planter."""
    depot.pose_etat(
        graphql=[
            {"contient": ["collaborators("],
             "reponse": reponse_membres([("bea", 40, False, "active")])}
        ]
    )
    acheve = depot.lib("pick-reviewer", "bea", "1")
    assert acheve.returncode == 1
    assert "aucun relecteur humain disponible" in acheve.stderr


def etat_revue(depot: Depot, auteur: str, relecteurs: list[str]) -> None:
    depot.pose_etat(
        graphql=[
            {
                "contient": ["pullRequest(number:"],
                "reponse": {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "author": {"login": auteur},
                                "reviewRequests": {
                                    "nodes": [
                                        {"requestedReviewer": {"login": r}} for r in relecteurs
                                    ]
                                },
                            }
                        }
                    }
                },
            },
            {"contient": ["collaborators("], "reponse": reponse_membres(MEMBRES)},
        ]
    )


def test_set_reviewer_pose_un_humain_distinct_de_l_auteur(depot: Depot) -> None:
    etat_revue(depot, auteur="bea", relecteurs=[])
    acheve = depot.lib("set-reviewer", "12")
    assert acheve.returncode == 0, acheve.stderr
    assert "relecteur → @" in acheve.stdout
    pose = appel(depot, "api", "-X", "POST")
    assert pose.split("\t")[3].endswith("/pulls/12/requested_reviewers")
    assert champs_api(pose)["reviewers[]"] in {"cam", "dan"}


def test_set_reviewer_ne_remplace_jamais_un_relecteur_deja_pose(depot: Depot) -> None:
    """Idempotent et non destructif : un relecteur posé à la main survit à un second passage."""
    etat_revue(depot, auteur="bea", relecteurs=["dan"])
    acheve = depot.lib("set-reviewer", "12")
    assert acheve.returncode == 0, acheve.stderr
    assert "relecteur déjà posé (@dan) — inchangé" in acheve.stdout
    assert [a for a in depot.appels() if a.startswith("mr\tupdate")] == []


def test_set_reviewer_refuse_de_designer_l_auteur(depot: Depot) -> None:
    etat_revue(depot, auteur="bea", relecteurs=[])
    acheve = depot.lib("set-reviewer", "12", "bea")
    assert acheve.returncode == 1
    assert "est l'auteur de la PR" in acheve.stderr
    assert [a for a in depot.appels() if a.startswith("mr\tupdate")] == []


def test_aucune_commande_ne_pose_de_relecteur_automatiquement() -> None:
    """#196 : la pose d'un relecteur reste outillée, mais n'est plus AUTOMATIQUE.

    Le helper `set-reviewer` continue d'exister et de fonctionner (tests ci-dessus) ; ce qui
    disparaît, c'est son **appel** par le cycle de clôture — désigner un relecteur attribue une PR
    à quelqu'un qui ne l'a pas demandé, alors que la file de revue porte déjà le signal. Cette
    règle vit dans des **prompts** (`.claude/commands/*.md`), pas dans du code : seule une lecture
    de ces fichiers peut la garder. On cherche donc une *invocation* (une ligne de commande), pas
    une mention — les commandes ont le droit de nommer le helper pour dire de ne pas l'appeler.
    """
    invocations: list[str] = []
    for commande in sorted((RACINE / ".claude" / "commands").glob("*.md")):
        lignes = commande.read_text(encoding="utf-8").splitlines()
        for numero, ligne in enumerate(lignes, 1):
            nue = ligne.strip()
            appel_helper = nue.startswith("bash ") and "set-reviewer" in nue
            appel_direct = nue.startswith("gh ") and "reviewer" in nue
            if appel_helper or appel_direct:
                invocations.append(f"{commande.name}:{numero}: {nue}")
    assert invocations == [], (
        "pose automatique de relecteur réintroduite (#196) :\n" + "\n".join(invocations)
    )


def invocations_des_commandes() -> list[tuple[str, int, str]]:
    """Les lignes de `.claude/commands/*.md` qui sont des APPELS, pas de la prose.

    Même heuristique que le test #196 ci-dessus, et pour la même raison : ces commandes ont le
    droit — le devoir, même — de *nommer* une forme interdite pour dire de ne pas l'employer. Seule
    une ligne qui commence par le verbe est une prescription.
    """
    lignes: list[tuple[str, int, str]] = []
    for commande in sorted((RACINE / ".claude" / "commands").glob("*.md")):
        for numero, ligne in enumerate(commande.read_text(encoding="utf-8").splitlines(), 1):
            nue = ligne.strip()
            if nue.startswith(("bash ", "git ", "gh ", "npm ")):
                lignes.append((commande.name, numero, nue))
    return lignes


def test_aucune_commande_ne_prescrit_de_forme_immatchable() -> None:
    """#233 : la couche permissions découpe un appel sur ses SAUTS DE LIGNE et ne matche aucune
    SUBSTITUTION `$(…)`.

    Une commande prescrite sous l'une de ces deux formes est refusée *alors même que* le verbe est
    autorisé, et le refus tombe sans personne pour l'accorder : 10 fois sur 8 sessions autonomes,
    toujours sur la **dernière action du ticket**, quand tout est commité et que rien ne le
    déclare. La règle vit dans des **prompts**, pas dans du code — seule une lecture de ces
    fichiers peut la garder.
    """
    fautives = [
        f"{nom}:{numero}: {nue}"
        for nom, numero, nue in invocations_des_commandes()
        if "$(" in nue or "<<" in nue
    ]
    assert fautives == [], (
        "forme immatchable réintroduite dans un prompt (#233) — passer par un fichier :\n"
        + "\n".join(fautives)
    )


def test_le_cycle_de_cloture_appelle_les_helpers_de_creation() -> None:
    """Le pendant POSITIF du test ci-dessus, et la leçon de !198 : livrer `create-mr` ne sert à
    rien tant que rien ne l'appelle.

    Cette MR-là avait ajouté les trois helpers à `lib.sh` — testés, fonctionnels — sans toucher aux
    prompts, restés sur la création de MR à description multi-ligne. Tout était vert : aucun test
    ne regardait le **raccordement**. C'est ce trou-ci que ce test bouche.
    """
    appels = [
        (nom, nue) for nom, _, nue in invocations_des_commandes() if "lib.sh create-mr" in nue
    ]
    assert [nom for nom, _ in appels] == ["ticket-finish.md"], (
        "`/ticket-finish` doit être le seul à créer la PR, et il doit le faire par le helper "
        f"(#233) — trouvé : {appels}"
    )
    assert not [
        f"{nom}:{numero}: {nue}"
        for nom, numero, nue in invocations_des_commandes()
        if "pr create" in nue
    ], "un `gh pr create` direct est réapparu : son corps est multi-ligne, donc refusé"


def test_branch_cleanup_delegue_sa_boucle_au_helper() -> None:
    """#309 : la boucle « quel est l'état de la PR de cette branche ? » vit dans `lib.sh`.

    `/branch-cleanup` la décrivait en prose — une lecture de l'état de la PR par branche locale,
    soit ~3 500 octets réinjectés pour en tirer un mot, **~43 000 tokens** sur ce dépôt à chaque
    invocation (audit #304 §4.1, le plus gros gisement du lot). `cleanup-merged` fait la même chose
    en shell, avec le **même** garde-fou, et n'imprime qu'un bilan.

    Deux implémentations du même garde-fou, dont une en prose, c'est aussi la divergence que
    supprime la délégation : le jour où le garde-fou change, la prose ne suit pas. D'où la seconde
    assertion — plus aucune lecture de PR **prescrite** dans cette commande.

    Le discriminant entre prescription et citation est ici le bloc `>` d'en-tête : la commande a le
    droit — le devoir, même — de **nommer** la forme qu'elle remplace pour dire de ne pas y
    revenir, et c'est là qu'elle le fait. Ailleurs, une ligne qui nomme `gh pr` est une consigne.
    Même parti pris que les deux tests ci-dessus, dont l'heuristique est le début de ligne.
    """
    lignes = [
        (numero, nue)
        for nom, numero, nue in invocations_des_commandes()
        if nom == "branch-cleanup.md"
    ]
    assert any("lib.sh cleanup-merged" in nue for _, nue in lignes), (
        "/branch-cleanup doit appeler `lib.sh cleanup-merged` — trouvé : "
        f"{[nue for _, nue in lignes]}"
    )

    commande = RACINE / ".claude" / "commands" / "branch-cleanup.md"
    fautives = [
        f"branch-cleanup.md:{numero}: {nue}"
        for numero, ligne in enumerate(commande.read_text(encoding="utf-8").splitlines(), 1)
        if "gh pr " in (nue := ligne.strip()) and not nue.startswith(">")
    ]
    assert fautives == [], (
        "lecture `gh pr` réintroduite dans /branch-cleanup (#309) — passer par lib.sh "
        "(`mr-state` rend un mot, `cleanup-merged` fait toute la boucle) :\n" + "\n".join(fautives)
    )


def test_branch_cleanup_garde_les_trois_fonctions_hors_du_helper() -> None:
    """Le revers de la délégation : `cleanup-merged` ne fait pas TOUT (#309).

    Trois fonctions restent à la commande — basculer sur `main` quand la branche courante est
    mergée (le helper ne touche jamais à celle du clone principal), supprimer la branche
    **distante** restée là (case « Delete source branch » décochée au merge), et poser le cycle de
    vie « Terminé » (le merge ferme le ticket sans toucher à aucun label, docs/10 §3). Déléguer en
    les perdant au passage transformerait une économie de tokens en régression silencieuse.
    """
    texte = (RACINE / ".claude" / "commands" / "branch-cleanup.md").read_text(encoding="utf-8")
    manquantes = [
        forme
        for forme in ("git checkout main", "git push origin --delete", "reconcile-workflow")
        if forme not in texte
    ]
    assert manquantes == [], (
        "/branch-cleanup a perdu une fonction que `cleanup-merged` ne couvre pas (#309) : "
        f"{manquantes}"
    )


def test_review_queue_rend_la_plus_ancienne_d_abord_avec_son_anciennete(depot: Depot) -> None:
    aujourdhui = date.today()
    depot.pose_etat(
        graphql=[
            {
                "contient": ["pullRequests(states: OPEN, orderBy"],
                "reponse": {
                    "data": {
                        "repository": {
                            "pullRequests": {
                                "nodes": [
                                    {
                                        "number": 10,
                                        "title": "Draft: Socle du chantier",
                                        "createdAt": f"{aujourdhui - timedelta(days=6)}T09:00:00Z",
                                        "isDraft": True,
                                        "headRefName": "chore/201-socle",
                                        "author": {"login": "bea"},
                                        "reviewRequests": {"nodes": []},
                                        "commits": {"nodes": [
                                            {"commit": {"statusCheckRollup": {"state": "FAILURE"}}}
                                        ]},
                                    },
                                    {
                                        "number": 11,
                                        "title": "Écran de suivi",
                                        "createdAt": f"{aujourdhui - timedelta(days=1)}T09:00:00Z",
                                        "isDraft": False,
                                        "headRefName": "feat/202-ecran",
                                        "author": {"login": "cam"},
                                        "reviewRequests": {"nodes": [
                                            {"requestedReviewer": {"login": "dan"}}
                                        ]},
                                        "commits": {"nodes": [
                                            {"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}
                                        ]},
                                    },
                                ]
                            }
                        }
                    }
                },
            }
        ]
    )
    acheve = depot.lib("review-queue")
    assert acheve.returncode == 0, acheve.stderr
    lignes = colonnes(acheve.stdout)
    assert [ligne[0] for ligne in lignes] == ["10", "11"]           # la plus ancienne en tête
    assert [ligne[1] for ligne in lignes] == ["6", "1"]             # ancienneté en jours
    assert [ligne[2] for ligne in lignes] == ["draft", "ready"]
    assert [ligne[3] for ligne in lignes] == ["failed", "success"]
    assert [ligne[5] for ligne in lignes] == ["-", "dan"]           # relecteur, « - » si personne
    # Le préfixe « Draft: » est retiré du titre : l'information est déjà dans la colonne `etat`.
    assert lignes[0][7] == "Socle du chantier"


# =================================================================================================
# Retard sur origin/main (#163)
# =================================================================================================


def prepare_retard(depot: Depot, fichier_main: str) -> None:
    """Branche de ticket qui touche `fichier-a`, puis un commit sur origin/main."""
    depot.git("checkout", "--quiet", "-b", "chore/900-essai")
    depot.commit("fichier-a.txt", "travail du ticket\n", "chore(essai): travail\n\nRefs #900")
    depot.git("checkout", "--quiet", "main")
    depot.commit(fichier_main, "avancée de main\n", "chore(main): avancée")
    depot.git("push", "--quiet", "origin", "main")
    depot.git("checkout", "--quiet", "chore/900-essai")


def test_behind_main_ne_dit_rien_quand_la_branche_est_a_jour(depot: Depot) -> None:
    depot.git("checkout", "--quiet", "-b", "chore/900-essai")
    depot.commit("fichier-a.txt", "travail\n", "chore(essai): travail\n\nRefs #900")
    acheve = depot.lib("behind-main")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "à jour avec origin/main (1 commit(s) d'avance)" in acheve.stdout


def test_behind_main_annonce_un_rebase_serein_sans_fichier_commun(depot: Depot) -> None:
    prepare_retard(depot, fichier_main="fichier-b.txt")
    acheve = depot.lib("behind-main")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "en retard : 1 commit(s) derrière origin/main" in acheve.stdout
    assert "aucun fichier modifié des deux côtés" in acheve.stdout
    assert "git fetch origin main && git rebase origin/main" in acheve.stdout


def test_behind_main_signale_le_conflit_probable_et_nomme_les_fichiers(depot: Depot) -> None:
    """C'est le signal qui manquait sur les fichiers aimants à conflits (CLAUDE.md, docs/10…)."""
    prepare_retard(depot, fichier_main="fichier-a.txt")
    acheve = depot.lib("behind-main")
    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert "conflit probable — 1 fichier(s) modifié(s) des deux côtés" in acheve.stdout
    assert "- fichier-a.txt" in acheve.stdout


def test_behind_main_ne_rebase_ni_ne_pousse_jamais(depot: Depot) -> None:
    """Purement consultatif : un rebase appellerait un force-push, interdit (docs/10 §6)."""
    prepare_retard(depot, fichier_main="fichier-a.txt")
    avant = depot.git("rev-parse", "HEAD")
    depot.lib("behind-main")
    assert depot.git("rev-parse", "HEAD") == avant
    assert depot.git("branch", "--show-current") == "chore/900-essai"


def test_behind_main_sur_main_n_a_rien_a_comparer(depot: Depot) -> None:
    acheve = depot.lib("behind-main", "main")
    assert acheve.returncode == 0
    assert "rien à comparer" in acheve.stdout


# =================================================================================================
# Conflit RÉEL avec origin/main (#303)
# =================================================================================================
#
# `behind-main` ci-dessus répond « ces fichiers sont modifiés des deux côtés » ; `mr-conflict`
# répond « git sait-il fusionner ? ». La différence n'est pas cosmétique : c'est elle qui décide
# si /mr-fix va résoudre un conflit ou n'a rien à faire.


def prepare_meme_fichier_regions_disjointes(depot: Depot) -> None:
    """Le cas des fichiers aimants du dépôt : les deux côtés touchent CLAUDE.md, sans se croiser.

    C'est le contre-exemple qui justifie le helper — `behind-main` crie au conflit probable
    (le fichier est modifié des deux côtés) là où le merge passe tout seul.
    """
    lignes = [f"ligne {n}\n" for n in range(1, 21)]
    depot.commit("aimant.txt", "".join(lignes), "chore(base): fichier aimant")
    depot.git("push", "--quiet", "origin", "main")

    depot.git("checkout", "--quiet", "-b", "chore/900-essai")
    debut = lignes.copy()
    debut[0] = "ligne 1 — retouchée par le ticket\n"
    depot.commit("aimant.txt", "".join(debut), "chore(essai): en-tête\n\nRefs #900")

    depot.git("checkout", "--quiet", "main")
    fin = lignes.copy()
    fin[19] = "ligne 20 — retouchée par main\n"
    depot.commit("aimant.txt", "".join(fin), "chore(main): pied de fichier")
    depot.git("push", "--quiet", "origin", "main")
    depot.git("checkout", "--quiet", "chore/900-essai")


def test_mr_conflict_dit_propre_la_ou_behind_main_croit_au_conflit(depot: Depot) -> None:
    """La raison d'être du helper : `behind-main` est pessimiste, `merge-tree` tranche."""
    prepare_meme_fichier_regions_disjointes(depot)

    pessimiste = depot.lib("behind-main")
    assert pessimiste.returncode == 4, pessimiste.stdout + pessimiste.stderr
    assert "conflit probable" in pessimiste.stdout
    assert "- aimant.txt" in pessimiste.stdout

    reel = depot.lib("mr-conflict")
    assert reel.returncode == 0, reel.stdout + reel.stderr
    assert "se merge proprement dans origin/main" in reel.stdout


def test_mr_conflict_nomme_les_fichiers_reellement_en_conflit(depot: Depot) -> None:
    prepare_retard(depot, fichier_main="fichier-a.txt")
    acheve = depot.lib("mr-conflict")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "en conflit avec origin/main — 1 fichier(s)" in acheve.stdout
    assert "- fichier-a.txt" in acheve.stdout
    # La résolution proposée est un MERGE : un rebase appellerait un force-push (docs/10 §6).
    # `behind-main`, lui, propose bien `git rebase origin/main` — ici ce serait un contresens.
    assert "git merge origin/main" in acheve.stdout
    assert "git rebase" not in acheve.stdout


def test_mr_conflict_ne_touche_ni_a_l_arbre_ni_a_l_index(depot: Depot) -> None:
    """Lecture seule : ni checkout, ni index — d'où l'appel possible sur une branche non sortie."""
    prepare_retard(depot, fichier_main="fichier-a.txt")
    avant_tete = depot.git("rev-parse", "HEAD")
    avant_branche = depot.git("branch", "--show-current")

    depot.lib("mr-conflict")

    assert depot.git("rev-parse", "HEAD") == avant_tete
    assert depot.git("branch", "--show-current") == avant_branche
    assert depot.git("status", "--porcelain") == ""
    # Aucun appel GitLab : le verdict est purement local, donc disponible sans réseau ni compte.
    assert depot.appels() == []


def test_mr_conflict_juge_une_branche_qu_on_ne_sort_pas(depot: Depot) -> None:
    """Le cas d'usage réel de /mr-fix : juger la branche d'une PR depuis le clone principal."""
    prepare_retard(depot, fichier_main="fichier-a.txt")
    depot.git("checkout", "--quiet", "main")

    acheve = depot.lib("mr-conflict", "chore/900-essai")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "- fichier-a.txt" in acheve.stdout
    assert depot.git("branch", "--show-current") == "main"


def test_mr_conflict_sur_main_n_a_rien_a_merger(depot: Depot) -> None:
    acheve = depot.lib("mr-conflict", "main")
    assert acheve.returncode == 0
    assert "rien à merger" in acheve.stdout


def test_mr_conflict_ne_prend_pas_une_erreur_pour_un_conflit(depot: Depot) -> None:
    """Sans ancêtre commun, git rend 128 — le confondre avec le 1 d'un conflit (docs/10 §8.3)
    enverrait /mr-fix résoudre un merge impossible."""
    # `--orphan` seul suffit : le commit qui suit est une RACINE, donc sans ancêtre commun avec
    # main. Surtout, ne rien effacer de l'arbre — le dépôt jetable y porte le `lib.sh` sous test.
    depot.git("checkout", "--quiet", "--orphan", "chore/901-orpheline")
    depot.commit("orpheline.txt", "sans ancêtre\n", "chore(orpheline): première racine")

    acheve = depot.lib("mr-conflict")
    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert "merge impossible à évaluer" in acheve.stderr
    assert "en conflit" not in acheve.stdout


def test_mr_conflict_sans_branche_ni_argument_refuse_proprement(depot: Depot) -> None:
    tete = depot.git("rev-parse", "HEAD")
    depot.git("checkout", "--quiet", tete)          # HEAD détachée
    acheve = depot.lib("mr-conflict")
    assert acheve.returncode == 2, acheve.stdout + acheve.stderr
    assert "branche indéterminée" in acheve.stderr


# =================================================================================================
# Garde-fou de clôture : la session traite-t-elle bien ce ticket ? (#164)
# =================================================================================================


@pytest.mark.parametrize(
    ("branche", "attendu"),
    [
        ("chore/164-garde-fou-de-cloture", "164"),
        ("feat/6-boucle-orchestration", "6"),
        ("chore/164", "164"),           # slug toléré absent : c'est l'iid qui porte l'information
    ],
)
def test_branch_iid_lit_l_iid_du_nom_de_branche(depot: Depot, branche: str, attendu: str) -> None:
    assert depot.lib("branch-iid", branche).stdout.strip() == attendu


@pytest.mark.parametrize("branche", ["main", "master", "brouillon", "chore/sans-iid"])
def test_branch_iid_reste_muet_hors_convention(depot: Depot, branche: str) -> None:
    acheve = depot.lib("branch-iid", branche)
    assert acheve.returncode == 1
    assert acheve.stdout.strip() == ""


def test_close_guard_valide_une_session_coherente(depot: Depot) -> None:
    depot.git("checkout", "--quiet", "-b", "chore/164-garde-fou")
    depot.pose_etat(
        graphql=[regle_owner("En cours", [MOI])]
    )
    acheve = depot.lib("close-guard", "164")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "cohérents" in acheve.stdout
    assert f"dont moi ({MOI})" in acheve.stdout


def test_close_guard_detecte_le_decalage_ticket_branche(depot: Depot) -> None:
    """Le contrôle FORT : `/ticket-finish 158` depuis `chore/163-…` poserait la PR sur #158."""
    depot.git("checkout", "--quiet", "-b", "chore/163-retard-sur-main")
    depot.pose_etat(
        graphql=[regle_owner("En cours", [MOI])]
    )
    acheve = depot.lib("close-guard", "158")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "décalage ticket ↔ branche" in acheve.stdout
    assert "porte le ticket #163, pas #158" in acheve.stdout


def test_close_guard_signale_un_ticket_appartenant_a_un_tiers(depot: Depot) -> None:
    depot.git("checkout", "--quiet", "-b", "chore/164-garde-fou")
    depot.pose_etat(
        graphql=[regle_owner("En cours", ["bea"])]
    )
    acheve = depot.lib("close-guard", "164")
    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert "appartient à quelqu'un d'autre" in acheve.stdout


def test_close_guard_dit_la_coherence_invérifiable_sur_une_branche_sans_iid(depot: Depot) -> None:
    depot.pose_etat(
        graphql=[regle_owner("À faire", [])]
    )
    acheve = depot.lib("close-guard", "164")           # on est resté sur main
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "aucun iid dans son nom" in acheve.stdout


def test_close_guard_ne_prend_pas_une_lecture_muette_pour_un_feu_vert(depot: Depot) -> None:
    """Le sens du doute va vers le refus : ticket illisible ⇒ verdict partiel, pas « libre »."""
    depot.git("checkout", "--quiet", "-b", "chore/164-garde-fou")
    depot.pose_etat(graphql=[])
    acheve = depot.lib("close-guard", "164")
    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert "propriété de #164 : indéterminée" in acheve.stdout


def test_close_guard_n_ecrit_jamais_rien(depot: Depot) -> None:
    """Consultatif comme `behind-main` : il constate, l'appelant décide."""
    depot.git("checkout", "--quiet", "-b", "chore/163-retard-sur-main")
    depot.pose_etat(
        graphql=[regle_owner("En cours", ["bea"])]
    )
    depot.lib("close-guard", "158")
    appels = depot.appels()
    assert [a for a in appels if a.startswith(ECRITURES)] == []
    assert "mutation" not in "\n".join(appels)


# =================================================================================================
# Création depuis un fichier : PR et notes (#233, parent #232)
# =================================================================================================
# Le texte long d'une PR ou d'un commentaire est la SEULE chose qu'une session autonome ne peut pas
# faire tenir sur une ligne de commande, et les deux replis naturels sont pires que le mal : la
# couche permissions découpe un appel sur ses SAUTS DE LIGNE et ne matche aucune SUBSTITUTION
# `$(…)` (docs/10 §11.7). D'où ces helpers, qui prennent un CHEMIN — le `$(cat …)` survit, mais à
# l'INTÉRIEUR du script, où aucune permission ne s'applique.
#
# Ce que ces tests gardent : le contenu arrive INTACT (c'est tout l'intérêt du détour par un
# fichier), l'appel est IDEMPOTENT (la création est la dernière action du ticket, elle doit
# supporter d'être rejouée), et un refus n'écrit RIEN — une PR sans description se découvrirait à
# la revue, quand tout est déjà commité.

BRANCHE = "chore/237-tests-doc-appels-dune-session-autonome-a"

#: Le texte porte tout ce qui rend une commande immatchable — sauts de ligne, `$(…)`, backquotes,
#: heredoc — plus des accents et un em-dash (le mojibake de #141). Il doit ressortir tel quel : ce
#: qui casse une ligne de commande ne doit rien casser du tout quand il voyage par fichier.
DESCRIPTION = (
    "Closes #237\n"
    "\n"
    "## Checklist\n"
    "- [x] Respecte les conventions de branche/commit — docs/10-workflow-git.md\n"
    "- [ ] Tests ajoutés/mis à jour si applicable\n"
    "\n"
    "Formes que la ligne de commande ne supporterait pas : `$(cat fichier)`, `whoami`,\n"
    "un heredoc `<<'EOF'`, et des accents « à é ù ».\n"
)


def regle_titre(iid: int, titre: str) -> dict:
    """Réponse à la lecture du titre d'un ticket (`gl_issue_title`).

    UN champ demandé, un champ rendu : c'est ce que la lecture GraphQL achète sur le JSON REST
    d'un ticket, qui porte plusieurs `title` — celui du ticket et celui de son jalon — et où
    l'extraction prenait la PREMIÈRE occurrence, donc l'ordre des clés de l'API.
    """
    return {
        "contient": [f"issue(number:{iid}"],
        "reponse": {"data": {"repository": {"issue": {"title": titre}}}},
    }


def regle_mr_de_branche(iid: str | None) -> dict:
    """Réponse à la résolution « quelle PR ouverte porte cette branche ? » (`gl_mr_iid`)."""
    return {
        "contient": ["pullRequests(headRefName"],
        "reponse": {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": [] if iid is None else [{"number": int(iid)}],
                    }
                }
            }
        },
    }


def sur_une_branche(depot: Depot, branche: str = BRANCHE) -> None:
    depot.git("checkout", "--quiet", "-b", branche)


def appel(depot: Depot, *debut: str) -> str:
    """L'unique appel `gh` commençant par ces arguments — échoue s'il y en a zéro ou deux."""
    prefixe = "\t".join(debut)
    trouves = [ligne for ligne in depot.appels() if ligne.startswith(prefixe)]
    assert len(trouves) == 1, f"un seul {prefixe!r} attendu, reçu {len(trouves)} : {depot.appels()}"
    return trouves[0]


def chemins_appeles(depot: Depot, suffixe: str) -> list[str]:
    """Les appels `gh api` dont le CHEMIN se termine par ce suffixe (« /pulls », « /comments »).

    Le chemin est le 4e champ d'un `gh api -X <MÉTHODE> <chemin> …` journalisé : le lire par sa
    position vaut mieux qu'un `in` sur toute la ligne, qui matcherait aussi bien un corps de PR
    citant l'URL.
    """
    return [
        ligne
        for ligne in depot.appels()
        if (champs := ligne.split("\t"))[3:4] and champs[3].endswith(suffixe)
    ]


def champs_api(ligne: str) -> dict[str, str]:
    """Les champs « clé=valeur » d'un `gh api` journalisé (posés par `-f`/`-F`).

    Une seule fabrique plutôt qu'un `valeur_option` par appel : côté GitHub, tout ce qu'une
    écriture porte voyage sous cette forme, y compris le corps téléversé (que le double a résolu
    depuis son fichier).
    """
    champs: dict[str, str] = {}
    for champ in ligne.split("\t"):
        cle, sep, valeur = champ.partition("=")
        if sep and not cle.startswith("-"):
            champs[cle] = valeur
    return champs


def valeur_option(ligne: str, option: str) -> str:
    """La valeur qui suit `--option` dans un appel journalisé (arguments séparés par des TAB)."""
    champs = ligne.split("\t")
    assert option in champs, f"{option} absent de {champs}"
    return champs[champs.index(option) + 1]


def journalise(texte: str) -> str:
    """Le texte tel que le journal du gh factice le rend — sauts de ligne échappés.

    Aucun `rstrip` : `-F body=@fichier` téléverse le fichier tel quel, saut de ligne final compris.
    C'est un gain, et il vaut d'être épinglé — le détour par une substitution de commande, lui,
    mangeait les sauts de ligne FINAUX (et seulement ceux-là), soit la dernière altération que le
    passage par un fichier laissait encore passer.
    """
    return texte.replace("\n", "\\n")


def fichier_description(depot: Depot, contenu: str = DESCRIPTION) -> Path:
    chemin = depot.racine / "description-mr.md"
    chemin.write_text(contenu, encoding="utf-8", newline="\n")
    return chemin


def test_create_mr_ouvre_une_draft_avec_le_titre_du_ticket_et_le_fichier(depot: Depot) -> None:
    """Le cas nominal : titre lu dans la forge, description lue dans le fichier, PR en Draft.

    Draft est dans le contrat : un run produit N PR **à relire**, il ne dé-drafte ni ne merge
    jamais (docs/10 §11). La suppression de la branche au merge, elle, n'est plus un drapeau de
    l'appel : c'est un réglage du DÉPÔT (`delete_branch_on_merge`), dont `doctor.sh` surveille la
    dérive — un champ de moins à poser à chaque PR, et un endroit de moins où l'oublier.
    """
    depot.pose_etat(
        graphql=[
            regle_mr_de_branche(None),
            regle_titre(237, "Tests + doc : appels d'une session autonome — allowlist"),
        ],
    )
    sur_une_branche(depot)
    fichier = fichier_description(depot)

    acheve = depot.lib("create-mr", "237", str(fichier))
    assert acheve.returncode == 0, acheve.stderr

    ligne = appel(depot, "api", "-X", "POST")
    champs = champs_api(ligne)
    assert ligne.split("\t")[3].endswith("/pulls")
    assert champs["draft"] == "true"
    assert champs["base"] == "main"
    assert champs["head"] == BRANCHE
    assert champs["title"].startswith("Tests + doc")
    # L'em-dash du titre survit : il traverse la lecture puis un argument shell sans repasser par
    # un décodage approximatif (#141).
    assert "—" in champs["title"]


def test_create_mr_transmet_le_fichier_octet_pour_octet(depot: Depot) -> None:
    """Le cœur du détour par un fichier : ce qui casserait une ligne de commande passe intact.

    Sauts de ligne, `$(…)`, backquotes et heredoc arrivent LITTÉRAUX côté forge — non réévalués,
    non tronqués. Si quelqu'un « simplifiait » un jour le helper en passant le texte autrement,
    c'est ici que ça se verrait.
    """
    depot.pose_etat(graphql=[regle_mr_de_branche(None), regle_titre(237, "Titre")])
    sur_une_branche(depot)
    fichier = fichier_description(depot)

    assert depot.lib("create-mr", "237", str(fichier)).returncode == 0

    recue = champs_api(appel(depot, "api", "-X", "POST"))["body"]
    assert recue == journalise(DESCRIPTION)
    assert "$(cat fichier)" in recue, "la substitution n'a pas été réévaluée : c'est du texte"


def test_create_mr_met_a_jour_la_mr_deja_ouverte_au_lieu_d_echouer(depot: Depot) -> None:
    """Idempotence : la création est la DERNIÈRE action du ticket, elle doit se rejouer.

    Reprise de session, second passage après un commit de plus : `/ticket-finish` repasse ici et
    ne doit ni échouer ni ouvrir une seconde PR sur la même branche.
    """
    depot.pose_etat(graphql=[regle_mr_de_branche("77"), regle_titre(237, "Titre")])
    sur_une_branche(depot)
    fichier = fichier_description(depot)

    acheve = depot.lib("create-mr", "237", str(fichier))
    assert acheve.returncode == 0, acheve.stderr
    assert "#77" in acheve.stdout, "la PR retrouvée est nommée"
    assert "pull/77" in acheve.stdout, "l'URL reste rendue, comme à la création"

    assert not chemins_appeles(depot, "/pulls"), \
        "une seconde PR aurait été ouverte sur la même branche"
    maj = appel(depot, "api", "-X", "PATCH")
    assert maj.split("\t")[3].endswith("/pulls/77")
    assert champs_api(maj)["body"] == journalise(DESCRIPTION)


def test_create_mr_refuse_depuis_main_sans_rien_ecrire(depot: Depot) -> None:
    """`main` n'a pas de PR à ouvrir : le dire vaut mieux qu'un appel qui échouera plus loin."""
    depot.pose_etat(graphql=[regle_mr_de_branche(None), regle_titre(237, "Titre")])
    fichier = fichier_description(depot)

    acheve = depot.lib("create-mr", "237", str(fichier))
    assert acheve.returncode == 1
    assert "main" in acheve.stderr
    assert not ecritures(depot)


@pytest.mark.parametrize(
    ("nom", "contenu", "attendu"),
    [("absent.md", None, "introuvable"), ("vide.md", "", "vide")],
)
def test_create_mr_refuse_un_fichier_inutilisable(
    depot: Depot, nom: str, contenu: str | None, attendu: str
) -> None:
    """Une PR sans description est pire qu'aucune PR : le helper s'arrête AVANT d'écrire.

    Le fichier vide est le cas réel — un `Write` qui n'a rien écrit, ou le chemin de scratchpad
    d'une session précédente.
    """
    depot.pose_etat(graphql=[regle_mr_de_branche(None), regle_titre(237, "Titre")])
    sur_une_branche(depot)
    chemin = depot.racine / nom
    if contenu is not None:
        chemin.write_text(contenu, encoding="utf-8", newline="\n")

    acheve = depot.lib("create-mr", "237", str(chemin))
    assert acheve.returncode == 1
    assert attendu in acheve.stderr
    assert not ecritures(depot)


def test_create_mr_signale_un_titre_illisible_plutot_que_d_en_inventer_un(depot: Depot) -> None:
    """Sans titre, pas de PR : une PR intitulée « » ne se remarquerait qu'à la revue."""
    depot.pose_etat(graphql=[regle_mr_de_branche(None)], rest=[])
    sur_une_branche(depot)
    fichier = fichier_description(depot)

    acheve = depot.lib("create-mr", "237", str(fichier))
    assert acheve.returncode == 1
    assert "#237" in acheve.stderr
    assert not chemins_appeles(depot, "/pulls")


def test_issue_note_poste_le_fichier_tel_quel(depot: Depot) -> None:
    """Le pendant de `create-mr` : `-m "$(cat …)"` n'est pas matchable non plus (#186)."""
    note = "Note de travail — « à relire ».\n\nDeuxième paragraphe.\n"
    fichier = fichier_description(depot, note)

    acheve = depot.lib("issue-note", "237", str(fichier))
    assert acheve.returncode == 0, acheve.stderr
    poste = appel(depot, "api", "-X", "POST")
    assert poste.split("\t")[3].endswith("/issues/237/comments")
    assert champs_api(poste)["body"] == journalise(note)


def test_issue_note_refuse_un_fichier_vide_sans_rien_poster(depot: Depot) -> None:
    fichier = fichier_description(depot, "")
    acheve = depot.lib("issue-note", "237", str(fichier))
    assert acheve.returncode == 1
    assert "vide" in acheve.stderr
    assert not ecritures(depot)


def test_issue_title_rend_le_titre_en_utf8_intact(depot: Depot) -> None:
    """Lecture seule, et fidèle : c'est ce titre qui devient celui de la PR."""
    depot.pose_etat(graphql=[regle_titre(237, "Tests + doc — appels « autonomes » d'une session")])
    acheve = depot.lib("issue-title", "237")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.strip() == "Tests + doc — appels « autonomes » d'une session"
    assert not ecritures(depot)


def test_les_helpers_de_creation_sont_annonces_par_l_usage(depot: Depot) -> None:
    """Un helper qu'on ne trouve pas n'existe pas : c'est l'usage qui l'apprend à une session.

    Le refus d'une création de PR multi-ligne tombe **sans humain pour l'expliquer** ; la seule
    chose que la session puisse lire pour s'en sortir est la sortie d'usage de `lib.sh`.
    """
    usage = depot.lib().stderr
    for verbe in ("create-mr", "issue-note", "issue-title"):
        assert verbe in usage, f"{verbe} absent de l'usage de lib.sh"
    assert "fichier" in usage.lower()


# =================================================================================================
# « Quelqu'un s'occupe-t-il encore de ce ticket ? » — la détection des orphelins (#328, parent #327)
# =================================================================================================
#
# Ce que ces tests épinglent est le GARDE-FOU, et lui seul (les critères du lot le disent : le reste
# des tests part au lot final #330). L'asymétrie est le cœur du sujet — désigner à tort le ticket
# d'une session vivante coûte infiniment plus cher que de rater un orphelin d'un tour, puisque #329
# rendra l'orphelin prenable —, donc c'est le faux positif qui est traqué ici, pas le faux négatif.
#
# Deux sources à éprouver, et elles ne se valent pas : la CARTE DU PILOTE fait foi (elle nomme un
# processus vérifiable) là où la fraîcheur du worktree n'est qu'une DÉDUCTION. La carte ne prouve
# jamais la mort, seulement la vie.


def _worktree(depot: Depot, iid: str) -> Path:
    """Monte un worktree du dépôt jetable sur la branche du ticket, et rend son chemin."""
    chemin = depot.racine.parent / "worktrees" / f"{iid}-essai"
    depot.git("worktree", "add", "--quiet", "-b", f"chore/{iid}-essai", str(chemin), "main")
    return chemin


def _index_de(chemin: Path) -> Path:
    """L'index git du worktree — le témoin que touche tout `git add`/`commit`/`status`."""
    assert GIT is not None
    brut = subprocess.run(  # noqa: S603
        [GIT, "-C", str(chemin), "rev-parse", "--git-path", "index"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(brut) if Path(brut).is_absolute() else chemin / brut


def _silence(chemin: Path, secondes: int) -> None:
    """Recule TOUTES les dates d'écriture du worktree : c'est ça, « plus personne dessus ».

    Toutes, et pas seulement l'index : la mesure prend le maximum de trois témoins (index, fichiers
    rendus par `git status`, atelier de session). En reculer un seul laisserait les autres frais, et
    le verdict « vivant » tomberait pour une raison étrangère à ce que le test prétend éprouver.
    """
    quand = time.time() - secondes
    cibles = [_index_de(chemin), chemin]
    cibles += [f for f in chemin.rglob("*") if ".git" not in f.parts]
    for cible in cibles:
        try:
            os.utime(cible, (quand, quand))
        except OSError:      # un chemin disparu entre-temps ne doit pas casser le harnais
            pass


def _run_vivant(depot: Depot, iid: str, run_id: str = "20260811-090000") -> subprocess.Popen:
    """Un run tenant `iid` en vol : plan, témoin de session, et un vrai processus posant sa carte.

    La carte est écrite par `pilote.sh` lui-même, jamais fabriquée à la main — la naissance du
    processus est en ticks et ne se devine pas depuis Python, si bien qu'un test qui inventerait le
    format ne vérifierait plus que sa propre invention. Même recette que les tests de l'arrêt dans
    `test_orchestrate.py`, et pour la même raison : la vivacité d'un processus ne se simule pas.
    """
    assert BASH is not None
    dossier = depot.racine / ".maestro" / "orchestrate" / run_id
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "plan.tsv").write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n"
        f"1\t{iid}\t-\thaute\t-\tTicket en vol\n",
        encoding="utf-8", newline="\n",
    )
    # Témoin de session SANS ligne de bilan dans resume.tsv : c'est ce couple-là qui veut dire
    # « pris en main, pas encore jugé », donc « en vol » (même définition que status.sh).
    (dossier / f"{iid}.session").write_text("uuid-de-session\n", encoding="utf-8", newline="\n")

    script = depot.racine.parent / "faux-pilote.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'. "{(depot.racine / "scripts" / "orchestrate" / "pilote.sh").as_posix()}"\n'
        'pilote_ecrit "$1"\n'
        'sleep "$2"\n',
        encoding="utf-8", newline="\n",
    )
    script.chmod(0o755)
    proc = subprocess.Popen(  # noqa: S603
        [BASH, str(script), str(dossier), "120"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # On attend une carte COMPLÈTE et non seulement présente : le fichier apparaît dès l'ouverture
    # de la redirection, et `hote` est le dernier champ posé.
    carte = dossier / "pid"
    for _ in range(200):
        if carte.exists() and "hote=" in carte.read_text(encoding="utf-8", errors="replace"):
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("le pilote factice n'a jamais posé sa carte")


def _verdicts(acheve: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """La sortie `--tsv` en « iid -> verdict »."""
    return {ligne[0]: ligne[1] for ligne in colonnes(acheve.stdout)}


# --- Le garde-fou : ne jamais désigner le ticket d'une session vivante ----------------------------


def test_un_worktree_qui_vient_d_etre_ecrit_n_est_jamais_orphelin(depot: Depot) -> None:
    depot.pose_etat(graphql=regles_backlog({"328": "En cours"}))
    _worktree(depot, "328")

    acheve = depot.lib("reconcile-en-cours", "--tsv")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _verdicts(acheve) == {"328": "vivant"}
    assert "déduction" in acheve.stdout, "la source d'un verdict déduit doit être annoncée"


def test_un_silence_de_cinq_heures_reste_vivant(depot: Depot) -> None:
    """Le seuil est GÉNÉREUX à dessein, et c'est ce qu'il protège qui le fixe.

    Une session qui épuise la limite d'usage dort jusqu'à son reset sans rien écrire, et `run.sh`
    l'attend jusqu'à 5 h 30. Un seuil plus court déclarerait abandonné un ticket dont la session
    attend légitimement — exactement le faux positif que #329 transformerait en reprise à tort.
    """
    depot.pose_etat(graphql=regles_backlog({"328": "En cours"}))
    _silence(_worktree(depot, "328"), 5 * 3600)

    assert _verdicts(depot.lib("reconcile-en-cours", "--tsv")) == {"328": "vivant"}


def test_la_carte_du_pilote_protege_un_ticket_silencieux(depot: Depot) -> None:
    """LE test du lot : la carte fait foi, et elle l'emporte sur un worktree muet depuis longtemps.

    Une session peut très bien réfléchir des heures sans rien écrire — c'est précisément ce que la
    déduction ne sait pas distinguer d'une mort, et ce que la carte, elle, tranche.
    """
    depot.pose_etat(graphql=regles_backlog({"328": "En cours"}))
    _silence(_worktree(depot, "328"), 48 * 3600)
    proc = _run_vivant(depot, "328")
    try:
        acheve = depot.lib("reconcile-en-cours", "--tsv")
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert _verdicts(acheve) == {"328": "vivant"}
        assert "carte du pilote" in acheve.stdout
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_une_carte_de_pilote_morte_ne_prouve_rien(depot: Depot) -> None:
    """L'asymétrie, dans l'autre sens : la carte prouve la VIE, jamais la mort.

    Un pilote arrêté au `taskkill` laisse sa carte derrière lui (aucun trap ne s'exécute). Elle ne
    doit ni sauver le ticket — c'est justement le mode de mort qui fabrique l'orphelin — ni le
    condamner : c'est la déduction qui reprend la main, et elle seule.
    """
    depot.pose_etat(graphql=regles_backlog({"328": "En cours"}))
    _silence(_worktree(depot, "328"), 48 * 3600)
    dossier = depot.racine / ".maestro" / "orchestrate" / "20260810-141208"
    dossier.mkdir(parents=True)
    (dossier / "plan.tsv").write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n1\t328\t-\thaute\t-\tTicket\n",
        encoding="utf-8", newline="\n",
    )
    (dossier / "328.session").write_text("uuid\n", encoding="utf-8", newline="\n")
    # Une carte qui ne désigne personne : PID hors de portée, naissance impossible à confirmer.
    (dossier / "pid").write_text(
        "pid=4294967294\nwinpid=\nnaissance=1\nepoch=1\nhote=inconnu\n",
        encoding="utf-8", newline="\n",
    )

    acheve = depot.lib("reconcile-en-cours", "--tsv")
    assert _verdicts(acheve) == {"328": "orphelin"}
    assert "carte du pilote" not in acheve.stdout


def test_un_ticket_sans_worktree_ici_est_hors_de_portee_jamais_orphelin(depot: Depot) -> None:
    """La couverture est celle des worktrees de CETTE machine, et elle se dit.

    Un ticket travaillé sur le clone de quelqu'un d'autre n'apprend rien d'ici : le déclarer
    orphelin reviendrait à proposer de reprendre le travail d'un vivant.
    """
    depot.pose_etat(graphql=regles_backlog({"316": "En cours"}))

    acheve = depot.lib("reconcile-en-cours", "--tsv")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _verdicts(acheve) == {"316": "hors-portee"}


def test_seuls_les_tickets_en_cours_sont_examines(depot: Depot) -> None:
    """« En revue » a livré, « À faire » n'a jamais commencé : ni l'un ni l'autre n'est orphelin."""
    depot.pose_etat(
        graphql=regles_backlog({"325": "En revue", "326": "À faire", "328": "En cours"})
    )
    for iid in ("325", "326", "328"):
        _silence(_worktree(depot, iid), 48 * 3600)

    acheve = depot.lib("reconcile-en-cours", "--tsv")
    assert _verdicts(acheve) == {"328": "orphelin"}


# --- La détection elle-même, et ce qu'elle ne fait pas --------------------------------------------


def test_un_worktree_silencieux_sans_pilote_est_un_orphelin(depot: Depot) -> None:
    depot.pose_etat(graphql=regles_backlog({"325": "En cours"}))
    chemin = _worktree(depot, "325")
    _silence(chemin, 48 * 3600)

    acheve = depot.lib("reconcile-en-cours")
    assert acheve.returncode == 3, "un orphelin se dit aussi par le code de retour"
    assert "#325 orphelin" in acheve.stdout
    assert "déduction" in acheve.stdout, "une déduction s'annonce comme telle"
    assert chemin.exists(), "la détection ne retire jamais un worktree"


def test_la_detection_n_ecrit_rien_du_tout(depot: Depot) -> None:
    """Elle SIGNALE : ni label, ni assignation, ni worktree touchés. La reprise est #329."""
    depot.pose_etat(graphql=regles_backlog({"325": "En cours"}))
    _silence(_worktree(depot, "325"), 48 * 3600)

    depot.lib("reconcile-en-cours")
    assert not ecritures(depot)


def test_check_est_accepte_et_ne_change_rien(depot: Depot) -> None:
    """Le verbe est en lecture seule par nature ; refuser `--check` serait un piège de famille."""
    depot.pose_etat(graphql=regles_backlog({"325": "En cours"}))
    _silence(_worktree(depot, "325"), 48 * 3600)

    sans = depot.lib("reconcile-en-cours", "--tsv")
    avec = depot.lib("reconcile-en-cours", "--check", "--tsv")
    assert avec.returncode == sans.returncode
    assert _verdicts(avec) == _verdicts(sans) == {"325": "orphelin"}


def test_sauf_ecarte_le_ticket_qu_on_est_en_train_de_demarrer(depot: Depot) -> None:
    """`ensure` le passe : le ticket qu'on démarre est repris à l'instant même.

    Sans ça, /ticket-start sur un ticket laissé en plan la veille annoncerait comme orphelin celui
    qu'il est justement en train de reprendre — vrai une seconde, faux la suivante.
    """
    depot.pose_etat(graphql=regles_backlog({"325": "En cours", "328": "En cours"}))
    for iid in ("325", "328"):
        _silence(_worktree(depot, iid), 48 * 3600)

    acheve = depot.lib("reconcile-en-cours", "--tsv", "--sauf", "328")
    assert _verdicts(acheve) == {"325": "orphelin"}


def test_auto_se_tait_sans_orphelin_et_parle_avec(depot: Depot) -> None:
    """Le mode des points de passage : le silence est le cas normal (comme `gc --auto`)."""
    depot.pose_etat(graphql=regles_backlog({"328": "En cours"}))
    chemin = _worktree(depot, "328")

    muet = depot.lib("reconcile-en-cours", "--auto")
    assert muet.returncode == 0
    assert muet.stdout == ""

    _silence(chemin, 48 * 3600)
    bavard = depot.lib("reconcile-en-cours", "--auto")
    assert bavard.returncode == 3
    assert "#328 orphelin" in bavard.stdout
    assert "vivant" not in bavard.stdout, "en --auto, seuls les orphelins sont une nouvelle"


def _lectures(depot: Depot) -> list[str]:
    """Les allers vers la forge — `gh api …` seul, `gh auth token` étant une lecture LOCALE."""
    return [ligne for ligne in depot.appels() if ligne.startswith("api\t")]


def test_auto_borne_sa_lecture_aux_worktrees_au_lieu_de_paginer_le_projet(depot: Depot) -> None:
    """Le poste le plus lourd de `ensure`, et il n'était pas celui qu'on croyait (#602).

    `--auto` ne peut rendre QU'UN ORPHELIN, et « orphelin » se déduit d'un worktree présent ici :
    un ticket sans worktree sur cette machine est hors de portée, quel que soit son état. Partir
    des worktrees ne peut donc rien perdre — et ça évite de payer le backlog entier (résolution du
    projet, 5 pages d'items, issues ouvertes : sept allers, 29,5 s mesurées) pour une question qui
    ne porte que sur deux tickets.

    Le motif est prouvé avant de conclure : le backlog déclaré porte PLUSIEURS tickets « En cours »,
    dont un seul a un worktree ici. Sans cette moitié, « une lecture » serait vrai d'un backlog
    vide.
    """
    depot.pose_etat(
        graphql=regles_backlog({"325": "En cours", "326": "En cours", "328": "En cours"})
    )
    _silence(_worktree(depot, "328"), 48 * 3600)

    acheve = depot.lib("reconcile-en-cours", "--auto")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "#328 orphelin" in acheve.stdout
    # Les deux autres n'ont pas de worktree ici : hors de portée, donc muets — comme avant.
    assert "#325" not in acheve.stdout
    assert "#326" not in acheve.stdout

    lectures = _lectures(depot)
    assert len(lectures) == 1, f"un aller pour les iid qui ont un worktree, reçu : {lectures}"
    assert not any("states: [OPEN]" in ligne for ligne in lectures), (
        "le backlog ouvert n'a pas à être lu pour répondre sur deux worktrees"
    )


def test_le_recensement_garde_sa_lecture_d_ensemble(depot: Depot) -> None:
    """Le pendant du test ci-dessus, et la raison pour laquelle il n'y a pas UN seul chemin.

    Les modes humain et `--tsv` rendent un RECENSEMENT — avec ses lignes « hors de portée » et son
    compte des trois verdicts —, ce que la lecture bornée ne peut pas produire : elle ne voit que
    les tickets qui ont un worktree. Ils sont demandés explicitement, par quelqu'un qui lit, jamais
    sur le chemin d'un démarrage de ticket.
    """
    depot.pose_etat(graphql=regles_backlog({"325": "En cours", "328": "En cours"}))
    _worktree(depot, "328")

    acheve = depot.lib("reconcile-en-cours")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "#325" in acheve.stdout, "un « En cours » sans worktree ici reste recensé"
    assert "hors de portée" in acheve.stdout


def test_le_backlog_illisible_ne_fait_pas_conclure_a_l_orphelin(depot: Depot) -> None:
    """Ne rien savoir n'autorise rien — même règle que le ramassage devant une forge muette."""
    depot.pose_etat(graphql=[])

    acheve = depot.lib("reconcile-en-cours")
    assert acheve.returncode == 1
    assert "orphelin" not in acheve.stdout


def test_le_verbe_est_annonce_par_l_usage(depot: Depot) -> None:
    """Un helper qu'on ne trouve pas n'existe pas — et #329 partira de celui-ci."""
    usage = depot.lib().stderr
    assert "reconcile-en-cours" in usage
    assert "orphelin" in usage


# =================================================================================================
# La reprise : rendre un orphelin prenable, sur un geste explicite (#329, parent #327)
# =================================================================================================
#
# Le lot précédent DÉSIGNE, celui-ci REND PRENABLE — et « prenable » est une CONJONCTION, parce que
# le filtre de `queue.sh` en est une : « À faire » ET libre. Trois choses sont épinglées ici, et
# elles ne se valent pas :
#
#   1. LE GARDE-FOU — ne jamais reprendre un ticket vivant. C'est le seul défaut de ce lot qui
#      coûterait cher : reprendre le ticket d'une session en cours le lui retire (le prochain run
#      l'assigne à quelqu'un d'autre), là où rater un orphelin ne coûte qu'un tour.
#   2. LE TRAVAIL PRÉSERVÉ — la reprise n'écrit QUE dans GitLab. #316, c'est 2047 lignes commitées
#      et jamais poussées : un verbe qui « nettoierait » au passage détruirait exactement ce qu'il
#      est censé sauver.
#   3. LE BORNAGE — ses tests ne sont PAS différés au lot final (contrairement au reste), et c'est
#      dit dans le ticket : sans plafond, un ticket qui retombe à chaque run transforme la reprise
#      en boucle, sur un quota partagé. C'est la seule ligne qui sépare un geste d'un emballement.


def _regles_reprise(statuts: dict[str, str]) -> list[dict]:
    """Tout ce qu'il faut pour qu'une reprise aboutisse : le backlog, le contexte et la mutation."""
    return [
        regle_pose_status(),
        regle_owner("En cours", [MOI]),
        *regles_backlog(statuts),
    ]


def _mutations(depot: Depot) -> list[str]:
    """Les écritures reçues — ce qui distingue « repris » de « refusé ».

    DEUX appels depuis #365, et c'est la contrepartie du champ : l'état vit sur l'ITEM DE PROJET
    (mutation GraphQL) et l'assignation sur l'ISSUE (`PATCH /issues/:n`). Du temps des labels les
    deux tenaient dans le même PATCH — la conjonction était structurelle ; elle est désormais tenue
    par l'ORDRE d'écriture (cf. l'en-tête de st_liberer_ticket). On collecte donc les deux formes.
    """
    return [
        ligne for ligne in depot.appels()
        if "\t-X\tPATCH\t" in ligne or "updateProjectV2ItemFieldValue" in ligne
    ]


def _reprises(depot: Depot) -> list[str]:
    """Le NOMBRE de reprises abouties, là où `_mutations` rend le nombre d'écritures.

    Les deux ont divergé à #365 : une reprise coûte désormais deux appels (l'état, puis
    l'assignation). Compter les écritures pour compter les reprises ferait échouer un test sur un
    détail de plomberie — et, pire, le ferait passer si l'une des deux disparaissait. On compte donc
    la LIBÉRATION, qui est le geste terminal : une par reprise, aucune sur un refus.
    """
    return [ligne for ligne in depot.appels() if "\t-X\tPATCH\t" in ligne and "/issues/" in ligne]


def _valeurs(ecriture: str, prefixe: str) -> list[str]:
    """Les valeurs d'un champ répété de l'écriture (« labels[]=… », « assignees[]=… »)."""
    return [
        champ[len(prefixe):]
        for champ in ecriture.split("\t")
        if champ.startswith(prefixe)
    ]


def _registre(depot: Depot) -> Path:
    return depot.racine / ".maestro" / "orchestrate" / "reprises.tsv"


def _run_juge(
    depot: Depot, iid: str, verdict: str, raison: str, run_id: str = "20260810-141208"
) -> None:
    """Un run passé qui a jugé ce ticket — la moitié « d'où il sort » de la trace."""
    dossier = depot.racine / ".maestro" / "orchestrate" / run_id
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "resume.tsv").write_text(
        "# iid\tverdict\tmr\tduree_s\tcout_usd\traison\n"
        f"{iid}\t{verdict}\t-\t2702\t14.75\t{raison}\n",
        encoding="utf-8", newline="\n",
    )


def _orphelin(depot: Depot, iid: str, statuts: dict[str, str] | None = None) -> Path:
    """Un ticket « En cours » dont le worktree est muet depuis deux jours."""
    depot.pose_etat(graphql=_regles_reprise(statuts or {iid: "En cours"}))
    chemin = _worktree(depot, iid)
    _silence(chemin, 48 * 3600)
    return chemin


# --- Le garde-fou : la reprise ne prend rien à personne -------------------------------------------


def test_reprendre_refuse_un_ticket_dont_quelqu_un_s_occupe(depot: Depot) -> None:
    """LE test du lot. Un worktree écrit à l'instant : quelqu'un travaille, on ne touche à rien.

    Le coût de l'erreur est asymétrique et c'est ce qui fixe le sens du refus — reprendre un ticket
    vivant le retire à sa session (le prochain run le prend et l'assigne), rater un orphelin ne
    coûte qu'un tour de boucle.
    """
    depot.pose_etat(graphql=_regles_reprise({"325": "En cours"}))
    _worktree(depot, "325")

    acheve = depot.lib("reprendre-en-cours", "325")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "quelqu'un s'en occupe" in acheve.stdout
    assert _mutations(depot) == [], "un refus n'écrit RIEN côté GitLab"


def test_reprendre_refuse_un_ticket_hors_de_portee(depot: Depot) -> None:
    """Aucun worktree ici : ne rien savoir n'autorise rien — le ticket vit peut-être ailleurs.

    C'est la borne annoncée du dispositif (les worktrees de CETTE machine) tenue jusque dans le
    geste : elle ne se relâche pas au moment d'écrire.
    """
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours"}))

    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 3
    assert "hors de portée" in acheve.stdout
    assert "--force" in acheve.stdout, "un refus doit dire par où passer quand on sait, soi"
    assert _mutations(depot) == []


def test_reprendre_refuse_un_ticket_qui_n_est_pas_en_cours(depot: Depot) -> None:
    """« En revue » a livré, « À faire » n'a jamais commencé : il n'y a rien à reprendre."""
    depot.pose_etat(graphql=_regles_reprise({"325": "En revue"}))
    _silence(_worktree(depot, "325"), 48 * 3600)

    acheve = depot.lib("reprendre-en-cours", "325")
    assert acheve.returncode == 3
    assert "n'est pas « En cours »" in acheve.stdout
    assert _mutations(depot) == []


def test_force_passe_outre_le_verdict_et_le_dit(depot: Depot) -> None:
    """Le geste de qui sait quelque chose que la machine ignore — jamais en silence."""
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours"}))

    acheve = depot.lib("reprendre-en-cours", "--force", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert len(_reprises(depot)) == 1
    assert "--force" in acheve.stdout, "lever le garde-fou se dit, sinon la sortie ment"


# --- Le geste lui-même : « À faire » ET libre, en une seule mutation ----------------------------


def test_reprendre_remet_a_faire_et_libere_ensemble(depot: Depot) -> None:
    """La conjonction. Poser le cycle de vie sans libérer laisserait le ticket écarté par l'autre
    moitié du filtre de `queue.sh`, et l'inverse par la première — il resterait invisible.

    ⚠ CE N'EST PLUS « UNE SEULE MUTATION » DEPUIS #365, et le test le dit au lieu de le taire :
    l'état vit sur l'ITEM DE PROJET, l'assignation sur l'ISSUE, donc les deux ne peuvent plus
    voyager dans le même appel comme au temps des labels. Ce qui est épinglé ici est ce qui a pris
    sa place : les DEUX écritures partent, et l'ÉTAT D'ABORD — il peut refuser (ticket hors projet),
    et il vaut mieux refuser sans avoir touché à l'assignation.
    """
    _orphelin(depot, "316")

    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    mutations = _mutations(depot)
    assert len(mutations) == 2, f"deux écritures attendues (état puis assignation) : {mutations}"
    etat, assignation = mutations
    assert "updateProjectV2ItemFieldValue" in etat, "l'état s'écrit EN PREMIER"
    assert "issues/316" in assignation
    assert "assignees[]" in assignation, "libérer, c'est VIDER la liste des assignés"
    assert _valeurs(assignation, "assignees[]=") == [], "aucun assigné ne doit être réécrit"
    assert _valeurs(assignation, "labels[]=") == [], (
        "l'assignation ne touche plus aux labels : le cycle de vie n'y vit plus (#365)"
    )


def test_la_reprise_ne_touche_ni_au_worktree_ni_aux_commits(depot: Depot) -> None:
    """#316, c'est 2047 lignes commitées et jamais poussées : elles doivent être là après.

    Le ticket est explicite — « sans rien perdre » — et c'est ce qui distingue cette reprise d'un
    `gc` : le verbe n'écrit QUE dans GitLab, il ne connaît même pas de chemin à supprimer.
    """
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours"}))
    chemin = _worktree(depot, "316")
    (chemin / "travail-non-commite.txt").write_text("2047 lignes\n", encoding="utf-8", newline="\n")
    depot.git("-C", str(chemin), "add", "-A")
    depot.git("-C", str(chemin), "commit", "--quiet", "-m", "feat: travail jamais poussé")
    (chemin / "encore-en-chantier.txt").write_text("en cours\n", encoding="utf-8", newline="\n")
    sha = depot.git("-C", str(chemin), "rev-parse", "HEAD")
    # Le silence vient APRÈS le travail : un `git commit` touche l'index, donc rend le worktree
    # frais — le faire dans l'autre ordre ferait conclure « vivant » et le test mesurerait le
    # refus au lieu de la préservation.
    _silence(chemin, 48 * 3600)

    assert depot.lib("reprendre-en-cours", "316").returncode == 0

    assert chemin.exists(), "le worktree n'est jamais retiré"
    assert (chemin / "encore-en-chantier.txt").exists(), "le non-commité reste où il est"
    apres = depot.git("-C", str(chemin), "rev-parse", "HEAD")
    assert apres == sha, "le commit non poussé est intact"
    assert depot.git("-C", str(chemin), "branch", "--show-current") == "chore/316-essai"


def test_check_dit_ce_qu_il_ferait_sans_rien_ecrire(depot: Depot) -> None:
    """Cohérence de famille (`reconcile-workflow --check`, `setup --derive`) : voir avant d'agir."""
    _orphelin(depot, "316")

    acheve = depot.lib("reprendre-en-cours", "--check", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "passerait à « À faire »" in acheve.stdout
    assert _mutations(depot) == []
    assert not ecritures(depot)
    assert not _registre(depot).exists(), "un --check ne consigne rien non plus"


# --- La trace : d'où sortait le ticket, et combien de fois il est déjà revenu -------------------


def test_la_reprise_consigne_le_run_et_le_verdict_d_origine(depot: Depot) -> None:
    """« D'où sort ce ticket revenu à “À faire” ? » ne se répond nulle part ailleurs.

    Le ticket, lui, ne porte aucune trace de la session morte dessus : c'est le journal du run qui
    la porte, et il sera ramassé au bout de dix runs (#198) — d'où une trace qui lui survit.
    """
    _orphelin(depot, "316")
    _run_juge(depot, "316", "ECHEC", "timeout — session terminée sans clôture, 1 commit(s)")

    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "20260810-141208" in acheve.stdout and "ECHEC" in acheve.stdout

    ligne = _registre(depot).read_text(encoding="utf-8").splitlines()[-1].split("\t")
    assert ligne[1:5] == ["316", "20260810-141208", "ECHEC", "1"]

    lecture = depot.lib("reprises", "316")
    assert lecture.returncode == 0, lecture.stderr
    assert "#316" in lecture.stdout and "20260810-141208" in lecture.stdout

    # Le commentaire sur le ticket : l'autre moitié de la trace, celle qu'on lit dans GitLab des
    # semaines plus tard, sans la machine sous la main.
    assert chemins_appeles(depot, "/comments"), (
        "la reprise laisse un commentaire sur le ticket"
    )


def test_une_reprise_sans_run_d_origine_le_dit_au_lieu_d_inventer(depot: Depot) -> None:
    """#325 : session interactive laissée en plan, aucun journal — ne rien trouver est une
    réponse."""
    _orphelin(depot, "325")

    acheve = depot.lib("reprendre-en-cours", "325")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "aucun run" in acheve.stdout
    assert _registre(depot).read_text(encoding="utf-8").splitlines()[-1].split("\t")[2] == "-"


# --- Le bornage : une reprise est un geste, pas une boucle --------------------------------------


def test_le_plafond_arrete_un_ticket_qui_retombe_a_chaque_run(depot: Depot) -> None:
    """Le garde-fou dont les tests ne sont PAS différés (le ticket le dit, et voici pourquoi).

    Un ticket que chaque session fait tomber au même endroit repartirait à chaque run, brûlerait
    une session entière à chaque fois et redeviendrait orphelin : la reprise serait une boucle, sur
    un quota partagé. Le plafond ne l'interdit pas — il exige qu'on le demande.
    """
    _orphelin(depot, "316")
    for essai in (1, 2):
        assert depot.lib("reprendre-en-cours", "316").returncode == 0, f"reprise {essai}"

    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "plafond" in acheve.stdout
    assert len(_reprises(depot)) == 2, "la troisième reprise n'a rien écrit"

    forcee = depot.lib("reprendre-en-cours", "--force", "316")
    assert forcee.returncode == 0, forcee.stdout + forcee.stderr
    assert len(_reprises(depot)) == 3, "--force reste la porte de sortie, jamais silencieuse"


def test_le_plafond_se_compte_par_ticket(depot: Depot) -> None:
    """Deux tickets, deux compteurs — sans quoi un ticket bloquerait la reprise de son voisin."""
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours", "325": "En cours"}))
    for iid in ("316", "325"):
        _silence(_worktree(depot, iid), 48 * 3600)
    for _ in (1, 2):
        assert depot.lib("reprendre-en-cours", "316").returncode == 0

    assert depot.lib("reprendre-en-cours", "316").returncode == 3
    assert depot.lib("reprendre-en-cours", "325").returncode == 0


def test_le_plafond_est_reglable(depot: Depot) -> None:
    """Deux essais est un choix, pas une constante de la nature : il se déplace sans code."""
    _orphelin(depot, "316")
    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 0, acheve.stderr

    borne = depot.lib("reprendre-en-cours", "316", reglages={"MAESTRO_REPRISES_MAX": "1"})
    assert borne.returncode == 3
    assert "plafond 1" in borne.stdout


def test_plusieurs_tickets_en_un_appel_et_un_refus_n_arrete_pas_les_autres(depot: Depot) -> None:
    """/orchestrate reprend en UN appel ce que l'utilisateur a coché : un refus au milieu de la
    liste ne doit pas laisser la moitié du travail non fait.
    """
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours", "325": "En cours"}))
    _silence(_worktree(depot, "316"), 48 * 3600)
    _worktree(depot, "325")   # celui-là est vivant : il sera refusé

    acheve = depot.lib("reprendre-en-cours", "316", "325")
    assert acheve.returncode == 3, "un refus se dit par le code de retour, même partiel"
    assert "#316 repris" in acheve.stdout
    assert "quelqu'un s'en occupe" in acheve.stdout
    assert len(_reprises(depot)) == 1


def test_le_verbe_de_reprise_est_annonce_par_l_usage(depot: Depot) -> None:
    usage = depot.lib().stderr
    assert "reprendre-en-cours" in usage
    assert "reprises" in usage
    assert "--force" in usage


# =================================================================================================
# Lot final : les trois modes de mort, le travail préservé, le bornage qui dure (#330, parent #327)
# =================================================================================================
#
# Les deux lots précédents n'ont épinglé que leur logique critique — le garde-fou du ticket vivant
# (#328), la conjonction « À faire » ET libre et le plafond (#329). Le reste était différé ici, et
# ce reste a une forme : ce sont les trois questions auxquelles le chantier répond et qu'aucun test
# ne pose encore telles quelles.
#
#   1. LES TROIS MODES DE MORT. Le renversement de #327 — ne pas demander « ce run a-t-il échoué ? »
#      mais « quelqu'un s'en occupe-t-il encore ? » — vaut précisément parce qu'il ne dépend PAS du
#      journal. Il faut donc le jouer sur les trois états de journal que produisent les trois
#      façons de mourir, et vérifier qu'ils rendent le même verdict et trois origines différentes.
#   2. LE TRAVAIL PRÉSERVÉ. #329 vérifie que le worktree est encore là et que HEAD n'a pas bougé.
#      Ce qui restait à prouver est plus fort : que RIEN n'y a bougé — c'est le seul défaut de ce
#      dispositif qui serait irréparable, les 2047 lignes de #316 n'existant nulle part ailleurs.
#   3. LE BORNAGE QUI DURE. Le plafond ne vaut que s'il survit à ce qui balaie le journal (#198) et
#      qu'il ne se contourne pas en changeant de répertoire. Un compteur qu'un ménage remet à zéro
#      est un compteur qui n'existe pas, et il ne se remarquerait qu'au dixième run.
#
# S'y ajoute la DÉRIVE DOCTOR (#328, section 4d), qui n'avait aucun test : c'est la moitié
# « visibilité » du chantier, celle qui parle à quelqu'un qui ne démarre pas de ticket ce jour-là.


# --- Les trois modes de mort ----------------------------------------------------------------------
# Un mode de mort ne se distingue que par ce qu'il laisse dans le journal. Ces trois recettes sont
# donc trois états de `.maestro/orchestrate/`, et rien d'autre : le worktree, lui, est muet dans les
# trois cas — c'est bien le point, la détection n'a pas à savoir de quoi la session est morte.


def _mort_run_solde(depot: Depot, iid: str) -> None:
    """#316 : le run a jugé, et son verdict est un échec.

    C'est le mode que `--resume` ne rattrapera JAMAIS : `reprend_en_vol` exige un témoin de session
    ET aucune ligne de bilan, or il y en a une. Le run est en plus « terminé », donc même pas
    reprenable. Sans ce dispositif, le ticket et son travail sortent du champ de vision pour de bon.
    """
    dossier = depot.racine / ".maestro" / "orchestrate" / "20260810-141208"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"{iid}.session").write_text("uuid\n", encoding="utf-8", newline="\n")
    (dossier / "resume.tsv").write_text(
        "# iid\tverdict\tmr\tduree_s\tcout_usd\traison\n"
        f"{iid}\tECHEC\t-\t2702\t14.75\ttimeout — session terminée sans clôture, 1 commit(s)\n",
        encoding="utf-8", newline="\n",
    )


def _mort_pilote_tue(depot: Depot, iid: str) -> None:
    """Le pilote arrêté au `taskkill //F` : aucun trap, donc aucun verdict — et sa carte reste là.

    C'est LE mode qui fabrique les orphelins, et le seul où la carte du pilote pourrait faire
    conclure à tort : elle survit à son processus. Elle ne prouve que la vie, jamais la mort.
    """
    dossier = depot.racine / ".maestro" / "orchestrate" / "20260811-093000"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"{iid}.session").write_text("uuid\n", encoding="utf-8", newline="\n")
    (dossier / "pid").write_text(
        "pid=4294967294\nwinpid=\nnaissance=1\nepoch=1\nhote=inconnu\n",
        encoding="utf-8", newline="\n",
    )


def _mort_session_interactive(depot: Depot, iid: str) -> None:  # noqa: ARG001
    """#325 : une session interactive laissée en plan — ni journal, ni témoin, ni verdict.

    Rien à écrire, et c'est tout le propos : ce mode-là échappe par construction à tout ce qui lit
    `.maestro/orchestrate/`. Seule la question posée au WORKTREE peut l'attraper.
    """


MODES_DE_MORT = [
    pytest.param(_mort_run_solde, "20260810-141208", "ECHEC", id="run-solde-en-echec"),
    pytest.param(_mort_pilote_tue, "20260811-093000", "sans verdict", id="pilote-tue-sans-verdict"),
    pytest.param(_mort_session_interactive, None, None, id="session-interactive-abandonnee"),
]


@pytest.mark.parametrize(("poser_le_journal", "run", "verdict_run"), MODES_DE_MORT)
def test_les_trois_modes_de_mort_produisent_un_orphelin_reprenable(
    depot: Depot,
    poser_le_journal,  # noqa: ANN001
    run: str | None,
    verdict_run: str | None,
) -> None:
    """LE critère du lot, et la thèse de #327 en un test : le mode de mort n'entre pas en compte.

    Trois journaux radicalement différents — un verdict d'échec, une carte de pilote sans verdict,
    rien du tout — pour un seul et même verdict, parce que la question n'est plus posée au run mais
    au worktree. Ce que le journal change, c'est seulement ce qu'on saura DIRE de l'origine : et
    « aucun run ne l'a jugé » est une réponse, pas un trou.
    """
    chemin = _orphelin(depot, "316")
    poser_le_journal(depot, "316")

    detection = depot.lib("reconcile-en-cours", "--tsv")
    assert _verdicts(detection) == {"316": "orphelin"}, detection.stdout + detection.stderr

    reprise = depot.lib("reprendre-en-cours", "316")
    assert reprise.returncode == 0, reprise.stdout + reprise.stderr
    assert len(_reprises(depot)) == 1
    assert chemin.exists(), "aucun mode de mort ne justifie de retirer le worktree"

    if run is None:
        assert "aucun run" in reprise.stdout
        assert _registre(depot).read_text(encoding="utf-8").splitlines()[-1].split("\t")[2] == "-"
    else:
        assert run in reprise.stdout and verdict_run in reprise.stdout
        assert _registre(depot).read_text(encoding="utf-8").splitlines()[-1].split("\t")[2] == run


# --- Le travail préservé --------------------------------------------------------------------------


def _empreinte(depot: Depot, chemin: Path) -> dict[str, object]:
    """Tout ce qu'une reprise pourrait abîmer, en un objet comparable.

    Le contenu des fichiers ET l'état de git : un verbe qui « nettoierait » au passage se verrait
    aussi bien par un fichier disparu que par un `git checkout` qui ne laisse aucune trace dans
    l'arborescence — d'où HEAD, la branche courante, la liste des branches et le `status`.
    """
    return {
        "fichiers": {
            f.relative_to(chemin).as_posix(): f.read_bytes()
            for f in sorted(chemin.rglob("*"))
            if f.is_file() and ".git" not in f.relative_to(chemin).parts
        },
        "head": depot.git("-C", str(chemin), "rev-parse", "HEAD"),
        "branche": depot.git("-C", str(chemin), "branch", "--show-current"),
        "branches": depot.git("-C", str(chemin), "branch", "--format=%(refname:short)"),
        "status": depot.git("-C", str(chemin), "status", "--porcelain"),
    }


def test_la_reprise_ne_change_rien_du_tout_dans_le_worktree(depot: Depot) -> None:
    """La promesse « sans rien perdre », prise au mot : l'empreinte est IDENTIQUE après.

    #329 vérifiait que le worktree était encore là et HEAD au même endroit ; c'est le minimum. Ce
    qui est en jeu ici n'a pas de sauvegarde : un commit jamais poussé n'existe que sur ce disque,
    et un fichier non commité n'existe même pas dans git. Le verbe n'écrit QUE dans GitLab — il ne
    connaît aucun chemin à supprimer —, et c'est cette phrase-là qu'on épingle.
    """
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours"}))
    chemin = _worktree(depot, "316")
    (chemin / "extraction.py").write_text("2047 lignes\n", encoding="utf-8", newline="\n")
    depot.git("-C", str(chemin), "add", "-A")
    depot.git("-C", str(chemin), "commit", "--quiet", "-m", "feat: jamais poussé")
    (chemin / "en-chantier.py").write_text("pas encore commité\n", encoding="utf-8", newline="\n")
    # L'atelier de session (#307) : gitignoré, invisible d'un `git status`, et pourtant c'est là
    # qu'une session pose ses fichiers de travail — donc là qu'une reprise pourrait faire le ménage.
    (chemin / ".maestro" / "session").mkdir(parents=True)
    (chemin / ".maestro" / "session" / "brouillon.md").write_text(
        "notes de la session morte\n", encoding="utf-8", newline="\n"
    )
    avant = _empreinte(depot, chemin)
    _silence(chemin, 48 * 3600)   # après le travail : un commit rafraîchit l'index (cf. #329)

    assert depot.lib("reprendre-en-cours", "316").returncode == 0

    assert _empreinte(depot, chemin) == avant, "la reprise n'écrit que dans GitLab"


def test_le_travail_est_preserve_meme_quand_le_garde_fou_est_leve(depot: Depot) -> None:
    """`--force` lève le VERDICT, jamais la préservation — les deux n'ont rien à voir.

    Le cas est réel : on force quand on sait que la session d'en face est morte pour de bon. Ce
    serait le pire moment pour que le verbe se permette un ménage, puisque c'est aussi celui où
    personne ne surveille plus le worktree.
    """
    depot.pose_etat(graphql=_regles_reprise({"316": "En cours"}))
    chemin = _worktree(depot, "316")
    (chemin / "en-chantier.py").write_text("396 lignes\n", encoding="utf-8", newline="\n")
    avant = _empreinte(depot, chemin)

    acheve = depot.lib("reprendre-en-cours", "--force", "316")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert _empreinte(depot, chemin) == avant


# --- Le bornage, mais celui qui dure --------------------------------------------------------------


def test_le_plafond_survit_au_menage_du_journal(depot: Depot) -> None:
    """Le registre vit à CÔTÉ des répertoires de run, et c'est ce qui lui donne sa valeur.

    `journal.sh gc` ne garde que les dix derniers runs (#198). Un compteur rangé dans le
    répertoire d'un run disparaîtrait donc avec lui — et un ticket qui retombe à chaque run
    repartirait indéfiniment, le plafond se remettant à zéro tous les dix runs. Le défaut ne se
    verrait qu'au dixième, et sur un quota partagé.
    """
    _orphelin(depot, "316")
    for essai in (1, 2):
        assert depot.lib("reprendre-en-cours", "316").returncode == 0, f"reprise {essai}"
    assert _registre(depot).exists()

    # Douze runs, tous plus vieux que le seuil de silence : le ménage en emportera deux.
    orch = depot.racine / ".maestro" / "orchestrate"
    vieux = time.time() - 30 * 24 * 3600
    for n in range(12):
        dossier = orch / f"2026070{n // 10}-1200{n % 10:02d}"
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "resume.tsv").write_text("# iid\n", encoding="utf-8", newline="\n")
        os.utime(dossier / "resume.tsv", (vieux, vieux))
        os.utime(dossier, (vieux, vieux))

    menage = subprocess.run(  # noqa: S603
        [BASH, str(depot.racine / "scripts/orchestrate/journal.sh"), "gc", "--auto"],
        cwd=str(depot.racine), capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert menage.returncode == 0, menage.stdout + menage.stderr
    # Le ménage a bien tourné : sans cette ligne, le test passerait aussi bien si `gc` n'avait rien
    # trouvé à faire — et il ne prouverait plus rien du tout.
    assert "retiré" in menage.stdout, f"le ménage n'a rien ramassé : {menage.stdout!r}"

    assert _registre(depot).exists(), "le ménage ne balaie que les répertoires de run"
    acheve = depot.lib("reprendre-en-cours", "316")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "plafond" in acheve.stdout


def test_le_plafond_ne_se_contourne_pas_en_changeant_de_repertoire(depot: Depot) -> None:
    """Le registre est celui du CLONE PRINCIPAL, d'où qu'on appelle — comme le journal lui-même.

    Une reprise se demande aussi bien depuis un worktree que depuis le clone principal
    (`/orchestrate` tourne dans l'un, une session dans l'autre). Un compteur par répertoire de
    travail rendrait le plafond décoratif : il suffirait de changer de fenêtre pour repartir à zéro.
    """
    chemin = _orphelin(depot, "316")
    for essai in (1, 2):
        assert depot.lib("reprendre-en-cours", "316").returncode == 0, f"reprise {essai}"

    depuis_le_worktree = depot.lib("reprendre-en-cours", "316", cwd=chemin)
    assert depuis_le_worktree.returncode == 3, depuis_le_worktree.stdout
    assert "plafond" in depuis_le_worktree.stdout
    assert len(_reprises(depot)) == 2, "la troisième reprise n'a rien écrit, d'où qu'elle vienne"


# --- La dérive doctor : le voir sans démarrer de ticket -------------------------------------------
# Les trois points de passage du signalement sont ceux de `gc` — /ticket-start, /branch-cleanup, le
# démarrage d'un run —, donc tous trois sont des GESTES. `doctor.sh` est l'autre voie : celle de
# quelqu'un qui demande l'état du dispositif sans rien démarrer du tout.


def section_en_cours(sortie: str) -> str:
    """Isole la dérive 4d (jusqu'au titre de la section 5)."""
    debut = sortie.index("4. Dérive cycle de vie ↔ réalité")
    reste = sortie[debut:]
    suivant = reste.find("\n5. ")
    return reste if suivant < 0 else reste[:suivant]


def test_doctor_nomme_le_ticket_en_cours_dont_plus_personne_ne_s_occupe(depot: Depot) -> None:
    """La quatrième dérive, et la seule qui ne se voie nulle part ailleurs.

    Les trois autres se lisent dans la forge (une PR manquante, un ticket fermé, deux labels) ;
    celle-ci demande de regarder un disque. Sans elle, la seule façon d'apprendre qu'un ticket est
    abandonné est de démarrer un autre ticket.
    """
    depot.pose_etat(graphql=regles_backlog({"317": "En cours"}))
    _silence(_worktree(depot, "317"), 48 * 3600)

    section = section_en_cours(depot.doctor().stdout)
    assert "#317 orphelin" in section
    assert "plus personne dessus" in section
    # Nommer la réparation sans la jouer : même partage que la dérive « cycle de vie ↔ PR ».
    assert "reprendre-en-cours" in section


def test_doctor_ne_signale_rien_quand_tout_le_monde_est_vivant(depot: Depot) -> None:
    """Un bilan de santé qui alerte à vide n'est plus lu — et la portée s'annonce dans le ✓."""
    depot.pose_etat(graphql=regles_backlog({"317": "En cours"}))
    _worktree(depot, "317")

    section = section_en_cours(depot.doctor().stdout)
    assert "aucun ticket « En cours » abandonné" in section
    assert "orphelin" not in section
    assert "cette machine" in section, "la borne de couverture se dit, même quand tout va bien"


def test_doctor_devant_un_orphelin_ne_repare_rien(depot: Depot) -> None:
    """« Ce fichier ne fait que le nommer comme dérive » — c'est sa promesse, elle se garde ici.

    La raison est plus forte que pour les autres dérives : « orphelin » est une déduction, et
    reprendre le ticket d'une session vivante coûterait bien plus cher que de laisser un orphelin
    un jour de plus.
    """
    depot.pose_etat(graphql=regles_backlog({"317": "En cours"}))
    _silence(_worktree(depot, "317"), 48 * 3600)

    depot.doctor()
    assert not ecritures(depot)
    assert "mutation" not in "\n".join(depot.appels())


# =================================================================================================
# Le chargement de lib.sh ne paie aucun processus (#372)
# =================================================================================================
#
# `GL_ICI` désigne le répertoire de lib.sh pour atteindre ses voisins, et ne sert qu'aux TROIS
# lignes des verbes de reprise ci-dessus. Il se calculait par
# `$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)` — trois processus sous MSYS, payés à CHAQUE
# chargement du fichier, donc par worktree.sh, run.sh, queue.sh, doctor.sh, et par les milliers
# d'invocations qu'en font les suites d'outillage. Mesuré le 2026-08-19 : 47,0 ms sur les 57,9 ms
# de marginal d'un chargement, soit 81 % — pour trois lignes.
#
# Deux tests, parce qu'il y a deux façons de le casser, et que seule la première se voit.


def test_le_chargement_de_lib_sh_ne_paie_aucun_fork() -> None:
    """La forme EST le fond : une substitution de commande ici, et le coût revient en silence.

    Un contrôle sur le texte plutôt que sur le temps, à dessein — un seuil en millisecondes rendrait
    rouge une machine chargée et vert un défaut sur une machine oisive.
    """
    lignes = [
        ligne
        for ligne in (RACINE / "scripts" / "gitlab" / "lib.sh")
        .read_text(encoding="utf-8")
        .splitlines()
        if ligne.startswith("GL_ICI=")
    ]
    assert lignes, "GL_ICI n'est plus défini : ce test ne garde plus rien"
    for ligne in lignes:
        assert "$(" not in ligne and "`" not in ligne, (
            f"le chargement de lib.sh refork : {ligne!r} — c'est le défaut que #372 a retiré"
        )


@pytest.mark.parametrize(
    ("cwd", "chemin"),
    [
        (".", "scripts/gitlab/lib.sh"),          # la forme la plus courante
        (".", "./scripts/gitlab/lib.sh"),        # la même, préfixée
        ("scripts", "../scripts/gitlab/lib.sh"),  # relative et remontante
        ("scripts/gitlab", "lib.sh"),            # sans le moindre `/`
        ("tests", "ABSOLU"),                     # absolu POSIX, depuis ailleurs
        ("tests", "ABSOLU-WINDOWS"),             # absolu TOUT EN ANTISLASHS — voir plus bas
    ],
)
def test_gl_ici_atteint_ses_voisins_quelle_que_soit_la_facon_de_charger(
    cwd: str, chemin: str
) -> None:
    """L'autre façon de casser : aller plus vite en désignant le mauvais répertoire.

    Ce que `GL_ICI` doit garantir n'est pas une FORME de chemin (il peut porter un `./` ou un `..`,
    sans importance) mais que ses voisins s'y trouvent. C'est aussi pourquoi il est ancré au
    chargement et non résolu à l'usage : `${BASH_SOURCE[0]}` peut être relatif, et un appelant qui
    change de répertoire entre le `source` et l'appel résoudrait alors depuis le mauvais endroit.

    Le cas `ABSOLU-WINDOWS` est celui qui manquait, et il a coûté quatre tests rouges (#372) : le
    harnais de ce fichier lance `bash <racine>/scripts/gitlab/lib.sh` en passant une `WindowsPath`,
    donc un chemin sans un seul `/`. Un découpage sur le dernier `/` n'y coupe rien et `GL_ICI`
    retombe sur le répertoire courant ; `pilote.sh` et `journal.sh` deviennent introuvables, si bien
    qu'un ticket vivant est déclaré « orphelin » et qu'une reprise répond « aucun run ne l'a jugé ».
    Les cinq premiers cas étaient tous en slashs : aucun ne pouvait le voir.

    La cible passe par ARGV et non par interpolation dans le `-c` : un antislash y serait relu par
    bash comme une échappée.
    """
    assert BASH is not None
    lib = RACINE / "scripts" / "gitlab" / "lib.sh"
    if chemin == "ABSOLU":
        cible = lib.as_posix()
    elif chemin == "ABSOLU-WINDOWS":
        cible = str(lib)  # WindowsPath → antislashs, exactement ce que passe le harnais
    else:
        cible = chemin
    acheve = subprocess.run(  # noqa: S603
        [
            BASH,
            "-c",
            '. "$1" && [ -r "$GL_ICI/../orchestrate/pilote.sh" ] && echo ATTEINT',
            "_",
            cible,
        ],
        cwd=str(RACINE / cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert "ATTEINT" in acheve.stdout, acheve.stdout + acheve.stderr


# ================================================================================================
# LA JOINTURE DE TEMPS — l'historique importé et le suivi quotidien (#400)
# ================================================================================================
# L'import du backlog (#340) a écrit le temps passé de chaque ticket dans un commentaire
# `maestro:meta v1` (une ligne de clés) ; le suivi quotidien lit un `maestro:suivi:v1` (une clé par
# ligne). Rien ne lisait les deux, si bien qu'un ticket importé repartait de ZÉRO au premier log de
# temps et finissait avec deux commentaires disant chacun une partie du total — mesuré sur #212 :
# 9000 s d'un côté, « 0m » de l'autre.
#
# CE QUE CES TESTS ÉPINGLENT, ET QUI EST LE CONTENU DE LA DÉCISION : la réparation est du côté de la
# LECTURE. Le commentaire d'import n'est jamais réécrit — c'est une archive (lien GitLab, tableau,
# relevés détaillés), et une campagne de réécriture sur 352 tickets serait irréversible là où une
# lecture ne l'est pas. D'où deux assertions jumelles dans chaque test d'écriture : sur QUEL
# commentaire l'appel part, et sur quel autre il ne part PAS.

META_218 = (
    "<!-- maestro:meta v1 iid=218 temps_s=4500 debut=2026-08-04 echeance=2026-08-06"
    " assignes=MaestroAgents -->\n"
    "**Importé de GitLab** · [`#218`](https://gitlab.com/x/maestro/-/work_items/218)\n"
)
META_212 = (
    "<!-- maestro:meta v1 iid=212 temps_s=9000 debut=2026-08-03 echeance=2026-08-08 lies=207 -->\n"
    "**Importé de GitLab** · [`#212`](https://gitlab.com/x/maestro/-/work_items/212)\n"
)
ID_IMPORT = 5313906639
ID_SUIVI = 5325509519


def suivi(*lignes: str) -> str:
    """Un commentaire de suivi : bloc machine, puis le rendu humain que rien ne doit relire."""
    return (
        "<!-- maestro:suivi:v1\n"
        + "".join(f"{ligne}\n" for ligne in lignes)
        + "-->\n**⏱ Suivi Maestro** — début … · échéance … · temps passé **0m**\n"
    )


def commentaire(identifiant: int, corps: str, cree_le: str = "2026-08-17T08:55:04Z") -> dict:
    return {"databaseId": identifiant, "createdAt": cree_le, "body": corps}


def regle_commentaires(*noeuds: dict) -> dict:
    """La réponse à la lecture du fil, DANS LA FORME D'OCTETS DE L'API RÉELLE.

    GitHub échappe `<` et `>` en `\\u003c` / `\\u003e` — or les deux marqueurs vivent dans un
    commentaire HTML. Rendre du `<` littéral ferait passer ces tests sur une forme que la
    production n'envoie jamais, et laisserait vert un motif qui chercherait le « <!-- ».
    """
    charge = json.dumps(
        {"data": {"repository": {"issue": {"comments": {"nodes": list(noeuds)}}}}},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    charge = charge.replace("<", "\\u003c").replace(">", "\\u003e")
    return {"contient": ["comments(first: 100)"], "brut": charge + "\n"}


def corps_ecrit(appel: str) -> str:
    """Le corps d'un appel d'écriture, ré-assemblé depuis le journal du `gh` factice.

    Le double résout le `body=@<fichier>` (c'est ainsi que le texte long voyage, #233) puis échappe
    les sauts de ligne : aucun corps de suivi ne porte d'antislash, un simple `\\n` → saut de ligne
    suffit donc à l'inverser.
    """
    for arg in appel.split("\t"):
        if arg.startswith("body="):
            return arg[len("body=") :].replace("\\n", "\n")
    raise AssertionError(f"aucun corps dans cet appel : {appel}")


def entrees_importees(corps: str) -> list[str]:
    """Les lignes `log=` du bloc machine qui portent l'historique importé."""
    return [
        ligne
        for ligne in corps.splitlines()
        if ligne.startswith("log=") and ligne.endswith("Historique importé de GitLab")
    ]


def test_le_temps_importe_se_lit_sans_commentaire_de_suivi(depot: Depot) -> None:
    """LE test du ticket : un ticket qui ne porte QUE l'import rend déjà son temps et ses dates.

    Et il les rend sans rien écrire — c'est ce qui distingue une jointure de lecture d'une
    migration.
    """
    depot.pose_etat(graphql=[regle_commentaires(commentaire(ID_IMPORT, META_218))])

    assert depot.lib("get-time-spent", "218").stdout.strip() == "4500"
    assert depot.lib("get-start-date", "218").stdout.strip() == "2026-08-04"
    assert ecritures(depot) == []


def test_le_total_importe_n_est_pas_un_cycle_deja_loggue(depot: Depot) -> None:
    """`--hors-import` retranche l'historique : c'est cette forme que /ticket-finish interroge.

    Sans elle, le garde-fou d'idempotence de la commande (« du temps est-il déjà loggé ? »)
    répondrait « oui » sur un ticket importé où personne n'a encore travaillé, et avalerait en
    silence le temps de la session qui le termine — l'inverse de ce que la jointure acquiert.
    """
    depot.pose_etat(graphql=[regle_commentaires(commentaire(ID_IMPORT, META_218))])

    assert depot.lib("get-time-spent", "218").stdout.strip() == "4500"
    assert depot.lib("get-time-spent", "218", "--hors-import").stdout.strip() == "0"
    assert depot.lib("get-time-spent", "218", "--n-importe-quoi").returncode == 2


def test_un_log_sur_un_ticket_importe_ajoute_au_total_sans_toucher_a_l_archive(
    depot: Depot,
) -> None:
    depot.pose_etat(graphql=[regle_commentaires(commentaire(ID_IMPORT, META_218))])

    acheve = depot.lib("log-time", "218", "30m", "Cycle de dev (start->finish)")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    ecrits = ecritures(depot)
    assert len(ecrits) == 1, ecrits
    assert "issues/218/comments" in ecrits[0]              # un commentaire de suivi NEUF…
    assert f"issues/comments/{ID_IMPORT}" not in ecrits[0]  # … et l'archive intacte

    corps = corps_ecrit(ecrits[0])
    assert "log=2026-08-17|4500|Historique importé de GitLab" in corps
    assert "|1800|Cycle de dev (start->finish)" in corps
    assert "temps=6300" in corps
    # Les dates de l'import ont suivi, et le rendu humain est dérivé du même bloc.
    assert "debut=2026-08-04" in corps
    assert "echeance=2026-08-06" in corps
    assert "temps passé **1h 45m**" in corps


def test_le_ticket_qui_porte_les_deux_commentaires_les_fusionne(depot: Depot) -> None:
    """Le cas #212, tel qu'il existe en production : import à 9000 s, suivi à « 0m ».

    Les dates ne se fusionnent pas dans le même sens que le temps : celles du suivi l'emportent,
    un /ticket-start postérieur à la bascule les ayant reposées.
    """
    depot.pose_etat(
        graphql=[
            regle_commentaires(
                commentaire(ID_IMPORT, META_212, "2026-08-17T08:53:43Z"),
                commentaire(ID_SUIVI, suivi("debut=2026-08-18", "echeance=2026-08-23")),
            )
        ]
    )

    assert depot.lib("get-time-spent", "212").stdout.strip() == "9000"
    assert depot.lib("get-time-spent", "212", "--hors-import").stdout.strip() == "0"
    assert depot.lib("get-start-date", "212").stdout.strip() == "2026-08-18"

    assert depot.lib("log-time", "212", "30m", "Cycle de dev").returncode == 0
    ecrits = ecritures(depot)
    assert len(ecrits) == 1, ecrits
    assert f"issues/comments/{ID_SUIVI}" in ecrits[0]       # le suivi, réécrit EN PLACE…
    assert f"issues/comments/{ID_IMPORT}" not in ecrits[0]  # … l'archive, toujours intacte

    corps = corps_ecrit(ecrits[0])
    assert "temps=10800" in corps
    assert "echeance=2026-08-23" in corps
    assert "echeance=2026-08-08" not in corps


def test_la_fusion_ne_repose_jamais_deux_fois_l_historique(depot: Depot) -> None:
    """L'idempotence, et sa mémoire : l'entrée elle-même, reconnue à son résumé.

    Aucune clé de témoin à tenir d'accord avec la donnée qu'elle décrit — relire un ticket déjà
    fusionné ne doit ni doubler l'entrée, ni doubler le total.
    """
    depot.pose_etat(
        graphql=[
            regle_commentaires(
                commentaire(ID_IMPORT, META_218),
                commentaire(
                    ID_SUIVI,
                    suivi(
                        "debut=2026-08-04",
                        "echeance=2026-08-06",
                        "log=2026-08-17|4500|Historique importé de GitLab",
                        "log=2026-08-21|1800|Cycle de dev",
                        "temps=6300",
                    ),
                ),
            )
        ]
    )

    assert depot.lib("get-time-spent", "218").stdout.strip() == "6300"
    assert depot.lib("get-time-spent", "218", "--hors-import").stdout.strip() == "1800"

    assert depot.lib("log-time", "218", "30m", "Second cycle").returncode == 0
    corps = corps_ecrit(ecritures(depot)[0])
    assert len(entrees_importees(corps)) == 1, corps
    assert "temps=8100" in corps


def test_un_ticket_sans_import_ne_gagne_aucune_entree(depot: Depot) -> None:
    """La non-régression : un ticket né sur GitHub ne connaît aucun historique GitLab."""
    depot.pose_etat(graphql=[regle_commentaires(commentaire(ID_SUIVI, suivi("debut=2026-08-20")))])

    assert depot.lib("log-time", "400", "1h", "Cycle de dev").returncode == 0
    corps = corps_ecrit(ecritures(depot)[0])
    assert entrees_importees(corps) == []
    assert "temps=3600" in corps


def test_un_ticket_sans_aucun_commentaire_cree_son_suivi(depot: Depot) -> None:
    """L'autre non-régression : chercher DEUX marqueurs ne change rien quand il n'y en a aucun."""
    depot.pose_etat(graphql=[regle_commentaires()])

    assert depot.lib("set-dates", "400", "2026-08-21", "2026-08-26").returncode == 0
    ecrits = ecritures(depot)
    assert len(ecrits) == 1, ecrits
    assert "issues/400/comments" in ecrits[0]
    assert "debut=2026-08-21" in corps_ecrit(ecrits[0])


# =================================================================================================
# Le verdict « ce travail est-il soldé ? » nomme une PR, pas une MR (#403, parent #401)
# =================================================================================================
#
# `tests/test_worktree.py` IMPOSE la réponse de `lib.sh worktree-done` (`impose_verdicts`), et c'est
# le bon choix pour lui : son sujet est le ramassage, pas la forge. Mais personne d'autre ne
# l'appelait, si bien que la raison qu'il imprime n'était épinglée nulle part — ses stubs disaient
# déjà « PR #42 mergée » quand la production disait encore « MR !410 mergée », et les deux suites
# restaient vertes. C'est ce qui a fait survivre la ligne au lot 1 (#402), dont le balayage
# cherchait `\bMR\b` : un motif que `\tMR` ne satisfait pas, le `t` de la tabulation échappée
# étant un caractère de mot. Le trou n'était donc pas dans la relecture, il était dans le filet.
#
# Ces deux tests tiennent le chaînon manquant : le VRAI helper, contre le `gh` factice.


def _regle_pr(etat: str, numero: int = 42) -> dict:
    """La réponse du `gh` factice à la lecture des PR — de quoi rendre « etat<TAB>numéro<TAB>sha ».

    ⚠ SOUS ALIAS (`b0:`) depuis #602 : `worktree-done` passe par `gl_worktree_done_lot`, qui
    demande N branches en UNE lecture. Le double rend donc la forme GROUPÉE — celle que la
    production reçoit vraiment. Servir encore la forme unitaire laisserait ces deux tests verts sur
    une réponse que plus personne ne demande, c'est-à-dire un ✓ sur une question jamais posée.
    """
    return {
        "contient": ["pullRequests(headRefName:"],
        "reponse": {
            "data": {
                "repository": {
                    "b0": {
                        "nodes": [{"number": numero, "state": etat, "headRefOid": "a" * 40}]
                    }
                }
            }
        },
    }


def _regle_pr_lot(etats: dict[str, str]) -> dict:
    """La réponse du `gh` factice à `gh_mr_briefs` — N branches sous alias, en UNE lecture (#602).

    Le RANG fait le lien entre la question et la réponse : un nom de branche porte des « / » et des
    « - », qu'un alias GraphQL n'accepte pas. Le double doit donc numéroter comme la production
    numérote, sinon il validerait un appariement qui n'existe pas.
    """
    return {
        "contient": ["pullRequests(headRefName:"],
        "reponse": {
            "data": {
                "repository": {
                    f"b{rang}": {
                        "nodes": [{"number": 40 + rang, "state": etat, "headRefOid": "a" * 40}]
                    }
                    for rang, etat in enumerate(etats.values())
                }
            }
        },
    }


def test_worktree_done_nomme_une_pr_et_jamais_une_mr(depot: Depot) -> None:
    """Le cas nominal, celui que la console imprime à chaque `/ticket-start` : PR mergée.

    L'assertion porte sur les DEUX moitiés du vocabulaire — le mot et sa numérotation. GitLab
    numérotait ses MR avec « ! », GitHub numérote ses PR avec « # » : « PR !42 » serait un mot juste
    sur un identifiant faux, et c'est précisément la moitié qu'un remplacement mécanique oublie.
    """
    depot.pose_etat(graphql=[_regle_pr("MERGED")])

    acheve = depot.lib("worktree-done", "403", "chore/403-vocabulaire")

    assert acheve.returncode == 0, acheve.stderr
    verdict, sha, raison = acheve.stdout.rstrip("\n").split("\t")
    assert verdict == "fini"
    assert sha == "a" * 40
    assert raison == "PR #42 mergée"


def test_le_verdict_de_n_worktrees_tient_en_deux_lectures(depot: Depot) -> None:
    """Le second poste de `ensure` : la question était posée une fois par worktree (#602).

    Chaque appel était un sous-processus complet — chargement de lib.sh, vérification du jeton,
    puis une lecture de la PR et, si elle n'était pas mergée, une lecture du ticket. Soit jusqu'à
    DEUX ALLERS PAR WORKTREE, là où la question en demande deux EN TOUT. Le prix ne se voit pas sur
    un poste qui n'a qu'un worktree ; il se voit après un run, qui en laisse un par ticket traité.

    Trois paires, deux lectures : la PR de toutes les branches, puis l'état des tickets que la
    première n'a pas soldés. Le compte des paires est asserté d'abord — c'est lui qui rend la
    conclusion possible.
    """
    depot.pose_etat(
        graphql=[_regle_pr_lot({"chore/401-a": "MERGED"})],
        issues={"402": TICKET_SIMPLE, "403": TICKET_SIMPLE},
    )
    paires = ["401:chore/401-a", "402:chore/402-b", "403:chore/403-c"]
    assert len(paires) > 1, "un lot d'une paire ne prouverait rien"

    acheve = depot.lib("worktree-done-lot", *paires)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    verdicts = {ligne[0]: ligne[1] for ligne in colonnes(acheve.stdout)}
    assert verdicts == {"401": "fini", "402": "actif", "403": "actif"}

    lectures = _lectures(depot)
    assert len(lectures) == 2, f"une lecture de PR, une de tickets — reçu : {lectures}"


def test_un_lot_tout_merge_ne_coute_qu_une_lecture(depot: Depot) -> None:
    """Le cas nominal d'un run qui se solde tout mergé : la seconde lecture est SAUTÉE.

    Elle ne sert qu'à départager ce que la PR n'a pas tranché. La demander quand même serait un
    aller pour une liste vide — et c'est exactement le genre de réflexe que ce ticket corrige.
    """
    depot.pose_etat(
        graphql=[_regle_pr_lot({"chore/401-a": "MERGED", "chore/402-b": "MERGED"})]
    )

    acheve = depot.lib("worktree-done-lot", "401:chore/401-a", "402:chore/402-b")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert {ligne[1] for ligne in colonnes(acheve.stdout)} == {"fini"}
    assert len(_lectures(depot)) == 1, _lectures(depot)


def test_worktree_done_nomme_une_pr_dans_son_verdict_sans_merge(depot: Depot) -> None:
    """L'autre voie — pas de PR mergée, c'est le ticket qui tranche — le dit aussi en « PR ».

    Elle était déjà juste ; l'épingler est ce qui empêche qu'un prochain balayage la défasse en
    silence, le ramassage ne lisant que le premier champ de la ligne.
    """
    depot.pose_etat(graphql=[_regle_pr("OPEN")], issues={"403": TICKET_SIMPLE})

    acheve = depot.lib("worktree-done", "403", "chore/403-vocabulaire")

    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.rstrip("\n").endswith('ticket #403 « open » (PR « opened »)')
    assert "MR" not in acheve.stdout
