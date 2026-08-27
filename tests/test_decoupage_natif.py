"""Tests du chantier « découpage natif » (ticket #396, parent #389).

Lot final : les lots #390 à #395 ont différé leurs tests ici (docs/10 §5.1), à une exception près —
le **marqueur « (parallèle) »**, seule pièce du découpage que les sub-issues ne portent pas d'
elles-mêmes, dont la logique a été gardée dès #390 dans
[`test_collaboration.py`](test_collaboration.py). Ce module couvre le reste, et il le fait selon un
critère précis : **les invariants qui ne se rejouent pas**.

Ce que « non rejouable » veut dire ici, et pourquoi c'est le bon filtre. Un chantier de migration
laisse deux sortes de propriétés derrière lui. Les unes se revérifient à volonté — « `subtickets`
lit-il les lots ? » se repose à chaque appel, et le premier `/ticket-start` venu répondrait. Les
autres ne se reposent qu'**une fois** : l'ordre des 41 parents a été posé le jour du backfill
(#392), le régime `checklist` a disparu avec #395, et la relation parent/lot d'un ticket déjà
rattaché ne se rattache pas une seconde fois. Celles-là, personne ne les rejouera — d'où quatre
familles, une par section :

* **l'ordre** (#391) — `subticket-order` : les lots nommés se retrouvent contigus et dans l'ordre
  donné, en une seule mutation, et **rien n'est écrit** quand la liste est fautive ;
* **l'idempotence** (#391) — `issue-link` / `subticket-add` : rejouer le rattachement d'un lot déjà
  rattaché à ce parent est un succès qui n'écrit rien. C'est ce qui rendait le backfill des 41
  parents relançable, et c'est ce qui reste testable de lui : le script one-shot est parti avec le
  support qu'il migrait, son idempotence vivait dans ce verbe ;
* **le double rattachement** (#391) — un lot déjà lot d'un AUTRE parent est refusé, et le refus
  nomme le parent en place. `addSubIssue` porte un `replaceParent` dont on ne se sert pas :
  déplacer un lot en silence est exactement ce qu'on ne veut pas ;
* **le verdict de `startables`** (#393) — le marqueur ayant changé de support, le verdict rendu sur
  un parent de référence devait rester le même. La comparaison des deux régimes n'est plus
  jouable — `checklist` est parti avec #395 —, donc ce qui est gardé ici est le verdict **figé**.

⚠ **Les mutations GraphQL sont invisibles d'`ecritures()`**, et c'est le premier piège de ce
module : la liste `ECRITURES` du harnais reconnaît les écritures REST (`gh api -X POST…`) et les
verbes `gh` (`issue edit`, `pr create`…), parce que c'est par là que passaient toutes les écritures
du dépôt quand elle a été écrite. Le découpage natif écrit par `gh api graphql -f query="mutation
{…}"`, qui ressemble à une lecture. D'où `mutations()` ci-dessous : sans lui, « le verbe n'a rien
écrit » serait vrai de tous les tests, y compris de ceux qui écrivent.

**Ni réseau ni compte de forge** : le harnais est celui de [`harnais_forge.py`](harnais_forge.py),
partagé avec `test_collaboration.py`, `test_cycle_de_vie.py` et `test_merge_automatique.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    GIT,
    Depot,
    colonnes,
    corps_ticket,
    ecritures,
    monte_depot,
    regle_statuts,
)

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


# =================================================================================================
# Le double, côté ÉCRITURE du découpage (#391)
# =================================================================================================
# Les fabriques de `harnais_forge` couvrent la LECTURE (la vue canonique d'un ticket, l'état de N
# lots, la carte du projet) ; celles-ci couvrent les deux requêtes que #391 a ajoutées, et elles
# vivent ici plutôt que dans le harnais parce qu'aucune autre suite ne les demande. Le jour où une
# seconde suite en aurait besoin, c'est là-bas qu'elles iront — la règle du dépôt étant qu'un double
# ne se recopie pas.
#
# LA FORME DES RÉPONSES EST DU CONTRAT, et pas seulement leur valeur : `gh_subticket_add` et
# `gh_subticket_order` parsent le JSON BRUT en grep/awk, sur des motifs qui collent des clés
# voisines (`"number":390,"lid":"…"`). Un double qui rendrait « le bon JSON autrement » validerait
# un parsing qui ne s'applique pas en vrai.

#: Identifiants de nœud opaques — l'API en rend, `lib.sh` n'en code aucun en dur (contrat du
#: fichier : tout se résout PAR NOM à chaque appel).
def node_id(iid: str) -> str:
    return f"I_kwDO_{iid}"


def regle_add(parent: str, lot: str, parent_actuel: str | None = None) -> dict:
    """La lecture préalable de `gh_subticket_add` : les deux ids de nœud ET le parent courant.

    UN SEUL ALLER, et deux alias (`p:` / `l:`) : sans eux la clé `id` désignerait à la fois le
    parent et le lot, et un `grep` global prendrait le premier trouvé — tantôt l'un, tantôt l'autre
    selon l'ordre de la réponse.

    `parent_actuel=None` décrit un lot LIBRE (`"parent":null`) : le motif `"ppnum":[0-9]*` ne matche
    alors rien, ce qui est exactement « ce lot n'a pas encore de parent ». C'est cette absence qui
    fait la différence entre le cas nominal et les deux cas gardés ci-dessous.
    """
    return {
        "contient": [f"p: issue(number:{parent})", f"l: issue(number:{lot})"],
        "reponse": {
            "data": {
                "repository": {
                    "p": {"pid": node_id(parent)},
                    "l": {
                        "lid": node_id(lot),
                        "parent": (
                            {"ppnum": int(parent_actuel)} if parent_actuel is not None else None
                        ),
                    },
                }
            }
        },
    }


def regle_lots_du_parent(parent: str, lots: tuple[str, ...]) -> dict:
    """La lecture préalable de `gh_subticket_order` : l'id du parent et celui de chaque lot.

    ⚠ `number` AVANT `lid`, collés par une virgule : l'awk du verbe apparie sur
    `"number":<n>,"lid":"<id>"` d'un seul motif, faute de quoi une paire coupée par un saut de ligne
    ferait disparaître un lot — donc échouer la validation sur un parent parfaitement sain.

    C'est ce que la vue texte de #390 ne peut PAS porter : elle transporte des numéros, les
    mutations veulent des identifiants de nœud. Les deux verbes lisent la même relation par deux
    chemins, sans que l'un puisse remplacer l'autre.
    """
    return {
        "contient": [f"issue(number:{parent})", "lots: subIssues(first: 100)"],
        "reponse": {
            "data": {
                "repository": {
                    "issue": {
                        "pid": node_id(parent),
                        "lots": {
                            "nodes": [
                                {"number": int(iid), "lid": node_id(iid)} for iid in lots
                            ]
                        },
                    }
                }
            }
        },
    }


def regle_mutation(nom: str, reponse: dict) -> dict:
    """Une mutation qui RÉUSSIT. `nom` est le fragment qui la distingue des lectures."""
    return {"contient": [nom], "reponse": {"data": reponse}}


def mutations(depot: Depot) -> list[str]:
    """Les ÉCRITURES GraphQL reçues par le double — ce qu'`ecritures()` ne voit pas.

    `ECRITURES` (harnais) reconnaît `gh api -X <MÉTHODE>` et les verbes `gh` de haut niveau : c'est
    la forme qu'avaient toutes les écritures du dépôt quand elle a été écrite. Une mutation GraphQL
    voyage par `gh api graphql -f query=…`, exactement comme une lecture — et « le verbe n'a rien
    écrit » serait donc vrai partout, y compris là où il écrit.

    Le motif est `mutation {` et non `mutation` seul : `reprioritizeSubIssue` et `addSubIssue`
    apparaissent aussi dans les COMMENTAIRES de `lib.sh`, jamais dans le journal du double, mais un
    motif qui dépendrait du nom de la mutation serait à retoucher au prochain verbe ajouté.
    """
    return [appel for appel in depot.appels() if "mutation {" in appel]


def lectures(depot: Depot) -> list[str]:
    """Les allers GraphQL en LECTURE — tout ce qui n'est pas une mutation."""
    return [
        appel
        for appel in depot.appels()
        if "graphql" in appel and "mutation {" not in appel
    ]


# =================================================================================================
# L'ordre des lots (#391) — ce qui fait le plan, et qui ne se rejoue pas
# =================================================================================================
# L'ordre n'est pas de l'affichage. `queue.sh` garde les lots d'un parent CONTIGUS et dans cet
# ordre-là, et `gl_subtickets_startables` juge « ce lot est-il démarrable ? » sur ce qui le PRÉCÈDE.
# Rattacher sans ordonner ne casserait pas l'affichage d'une page : ça ferait partir le dernier lot
# en premier, et le lot final « tests + doc » avant ce qu'il teste.

ORDRE_VOULU = ("390", "391", "392", "393")


def parent_a_ordonner(depot: Depot, lots: tuple[str, ...] = ORDRE_VOULU) -> None:
    """Un parent dont la forge rend les lots — dans un ordre quelconque, puisqu'on va le poser."""
    depot.pose_etat(
        graphql=[
            regle_lots_du_parent("389", lots),
            regle_mutation("reprioritizeSubIssue", {"m2": {"issue": {"number": 391}}}),
        ]
    )


def test_l_ordre_demande_voyage_en_une_seule_mutation(depot: Depot) -> None:
    """Une lecture, une écriture, quel que soit le nombre de lots.

    `reprioritizeSubIssue` ne déplace qu'UN lot à la fois, mais les champs de premier niveau d'une
    mutation GraphQL sont exécutés EN SÉRIE et dans l'ordre écrit : les N-1 déplacements voyagent
    donc sous alias dans un seul document. C'est la règle de #577/#602 — ne pas demander N fois ce
    qui se demande une fois — appliquée à une écriture.
    """
    parent_a_ordonner(depot)
    acheve = depot.lib("subticket-order", "389", *ORDRE_VOULU)
    assert acheve.returncode == 0, acheve.stderr
    assert len(lectures(depot)) == 1, lectures(depot)
    ecrits = mutations(depot)
    assert len(ecrits) == 1, ecrits


def test_chaque_lot_est_pousse_derriere_son_predecesseur(depot: Depot) -> None:
    """Le contenu de la mutation, alias par alias — c'est LUI qui restitue l'ordre.

    Trois déplacements pour quatre lots : le premier ne bouge pas, il est le point fixe dont tout le
    reste hérite sa place. Le test lit donc les couples (déplacé, après qui) et vérifie qu'ils
    chaînent la liste demandée — pas qu'ils « contiennent les bons identifiants », ce qui serait
    vrai d'un ordre inversé.
    """
    parent_a_ordonner(depot)
    depot.lib("subticket-order", "389", *ORDRE_VOULU)
    (mutation,) = mutations(depot)
    for rang, iid in enumerate(ORDRE_VOULU[1:], start=2):
        fragment = (
            f'm{rang}: reprioritizeSubIssue(input: {{issueId: "{node_id("389")}", '
            f'subIssueId: "{node_id(iid)}", afterId: "{node_id(ORDRE_VOULU[rang - 2])}"}})'
        )
        assert fragment in mutation, mutation
    # Le premier lot n'est déplacé par personne : trois alias pour quatre lots.
    assert mutation.count("reprioritizeSubIssue") == len(ORDRE_VOULU) - 1


def test_un_iid_etranger_au_parent_n_ecrit_rien_du_tout(depot: Depot) -> None:
    """Rien n'est écrit avant que tout soit validé, et c'est le point à ne pas défaire.

    Un ordre posé à moitié serait pire que pas d'ordre du tout : il laisserait le plan dans un état
    que personne n'a voulu, sur un parent que plus rien ne repasse.
    """
    parent_a_ordonner(depot)
    acheve = depot.lib("subticket-order", "389", "390", "999", "391")
    assert acheve.returncode == 1
    assert "#999" in acheve.stderr
    assert "Aucun ordre n'a été posé" in acheve.stderr
    assert mutations(depot) == []


def test_un_lot_nomme_deux_fois_n_ecrit_rien_du_tout(depot: Depot) -> None:
    """L'autre moitié de la validation : une liste qui se répète est fautive, pas « à dédupliquer ».

    Dédupliquer en silence poserait un ordre que l'appelant n'a pas demandé, et laisserait le lot
    répété à une place que rien ne désigne.
    """
    parent_a_ordonner(depot)
    acheve = depot.lib("subticket-order", "389", "390", "391", "390")
    assert acheve.returncode == 1
    assert "#390" in acheve.stderr
    assert mutations(depot) == []


def test_un_seul_lot_s_abstient_sans_parler_a_la_forge(depot: Depot) -> None:
    """L'ordre est une relation entre au moins deux éléments : à un lot, il n'y a rien à poser.

    L'abstention est SANS aller de forge, et ce n'est pas de l'économie : c'est ce qui rendait
    triviale la boucle du backfill (#392), qui rencontrait des parents à un lot sans avoir à les
    distinguer des autres.
    """
    parent_a_ordonner(depot)
    acheve = depot.lib("subticket-order", "389", "390")
    assert acheve.returncode == 0, acheve.stderr
    assert "rien à ordonner" in acheve.stdout
    assert depot.appels() == []


def test_un_iid_qui_n_en_est_pas_un_est_refuse_avant_la_lecture(depot: Depot) -> None:
    """La validation de forme passe avant tout, y compris avant la lecture des identifiants."""
    parent_a_ordonner(depot)
    acheve = depot.lib("subticket-order", "389", "390", "chore/391")
    assert acheve.returncode == 2
    assert depot.appels() == []


# =================================================================================================
# L'idempotence du rattachement (#391) — ce qui rendait le backfill relançable
# =================================================================================================
# Le backfill des 41 parents (#392) était un script one-shot, parti avec le support qu'il migrait
# (#395). Ce qui reste de lui est la propriété qui le rendait relançable, et elle n'a jamais vécu
# dans le script : `gl_subticket_add` LIT avant d'écrire, donc un parent déjà rattaché coûte une
# lecture et zéro mutation. C'est aussi ce qui permet à `/ticket-create` de rejouer `issue-link` sur
# un lot déjà lié sans que rien ne bouge.
#
# ⚠ `addSubIssue` rendrait bien une erreur sur un lot déjà parenté — mais son message ne nomme pas
# le parent en place, et c'est précisément ce qu'il faut dire à qui rattache le mauvais lot.


def test_un_lot_deja_rattache_a_ce_parent_est_un_succes_sans_ecriture(depot: Depot) -> None:
    """L'idempotence, mesurée là où elle se voit : la mutation n'est PAS rejouée."""
    depot.pose_etat(
        graphql=[
            regle_add("389", "390", parent_actuel="389"),
            regle_mutation("addSubIssue", {"addSubIssue": {"subIssue": {"number": 390}}}),
        ]
    )
    acheve = depot.lib("issue-link", "389", "390")
    assert acheve.returncode == 0, acheve.stderr
    assert "déjà rattaché" in acheve.stdout
    assert mutations(depot) == []
    assert len(lectures(depot)) == 1, lectures(depot)


def test_le_rattachement_d_un_lot_libre_ecrit_une_fois_et_une_seule(depot: Depot) -> None:
    """Le cas nominal, et son pendant : deux passages, une seule écriture au total.

    Le double n'a pas de mémoire — c'est le parti pris du harnais —, donc le second passage est
    décrit par la RÉPONSE qu'aurait la forge après le premier : le lot y porte son parent. C'est
    exactement la situation d'un backfill relancé, et la seule façon de la jouer sans inventer un
    double qui tiendrait un état.
    """
    depot.pose_etat(
        graphql=[
            regle_add("389", "390"),
            regle_mutation("addSubIssue", {"addSubIssue": {"subIssue": {"number": 390}}}),
        ]
    )
    premier = depot.lib("issue-link", "389", "390")
    assert premier.returncode == 0, premier.stderr
    assert "Lot rattaché : #390 → #389" in premier.stdout
    assert len(mutations(depot)) == 1

    depot.pose_etat(
        graphql=[
            regle_add("389", "390", parent_actuel="389"),
            regle_mutation("addSubIssue", {"addSubIssue": {"subIssue": {"number": 390}}}),
        ]
    )
    second = depot.lib("issue-link", "389", "390")
    assert second.returncode == 0, second.stderr
    assert len(mutations(depot)) == 1, mutations(depot)


def test_un_lot_deja_rattache_ailleurs_est_refuse_et_le_parent_est_nomme(depot: Depot) -> None:
    """Le double rattachement : refusé, et refusé en NOMMANT le parent en place.

    `addSubIssue` porte un `replaceParent` dont on ne se sert pas — remplacer un parent en silence
    est l'inverse de ce qu'on veut. Le verbe le refuse donc AVANT d'écrire, ce qui laisse le lot là
    où il est : un chantier ne perd pas un lot parce qu'un iid a été mal recopié.
    """
    depot.pose_etat(
        graphql=[
            regle_add("389", "390", parent_actuel="167"),
            regle_mutation("addSubIssue", {"addSubIssue": {"subIssue": {"number": 390}}}),
        ]
    )
    acheve = depot.lib("issue-link", "389", "390")
    assert acheve.returncode == 1
    assert "#390 est déjà un lot de #167" in acheve.stderr
    assert "détacher d'abord" in acheve.stderr
    assert mutations(depot) == []


def test_un_ticket_ne_peut_pas_etre_son_propre_lot(depot: Depot) -> None:
    """Un cycle n'a pas de lecture en aval : « quel lot précède celui-ci ? » n'aurait plus de fin.

    La forge le refuse aussi, mais après un aller et dans ses mots à elle — d'où le refus local,
    sans lecture.
    """
    depot.pose_etat(graphql=[regle_add("389", "389")])
    acheve = depot.lib("issue-link", "389", "389")
    assert acheve.returncode == 1
    assert "son propre lot" in acheve.stderr
    assert depot.appels() == []


def test_les_deux_absences_se_distinguent(depot: Depot) -> None:
    """« #390 introuvable » envoie vérifier le lot, « #389 introuvable » le parent.

    Ce n'est pas de la coquetterie : c'est le backfill qui lisait ces messages sur 41 parents, et
    un message unique aurait fait chercher du mauvais côté une fois sur deux.
    """
    depot.pose_etat(
        graphql=[
            {
                "contient": ["p: issue(number:389)"],
                "reponse": {"data": {"repository": {"p": None, "l": {"lid": node_id("390")}}}},
            }
        ]
    )
    parent_absent = depot.lib("issue-link", "389", "390")
    assert parent_absent.returncode == 1
    assert "Ticket #389 introuvable" in parent_absent.stderr

    depot.pose_etat(
        graphql=[
            {
                "contient": ["p: issue(number:389)"],
                "reponse": {"data": {"repository": {"p": {"pid": node_id("389")}, "l": None}}},
            }
        ]
    )
    lot_absent = depot.lib("issue-link", "389", "390")
    assert lot_absent.returncode == 1
    assert "Ticket #390 introuvable" in lot_absent.stderr


def test_le_marqueur_est_pose_apres_le_rattachement(depot: Depot) -> None:
    """`--parallele` : le rattachement d'abord, le label ensuite, et l'ordre est le contenu.

    Un lot rattaché sans son label est SÉQUENTIEL — le côté sûr (docs/10 §5.1) ; un label posé sur
    un lot que le rattachement a refusé ne désigne rien.
    """
    depot.pose_etat(
        graphql=[
            regle_add("389", "390"),
            regle_mutation("addSubIssue", {"addSubIssue": {"subIssue": {"number": 390}}}),
        ]
    )
    acheve = depot.lib("issue-link", "389", "390", "--parallele")
    assert acheve.returncode == 0, acheve.stderr
    assert "lot::parallele" in acheve.stdout
    poses = [appel for appel in ecritures(depot) if "lot::parallele" in appel]
    assert len(poses) == 1, ecritures(depot)
    # L'ordre, et pas seulement la présence des deux : la mutation part AVANT la pose du label.
    assert depot.appels().index(mutations(depot)[0]) < depot.appels().index(poses[0])


def test_un_rattachement_refuse_ne_pose_aucun_label(depot: Depot) -> None:
    """La conséquence de l'ordre : un label posé sur un lot resté chez un autre parent ne désigne
    rien, et il resterait là sans que rien ne le retire."""
    depot.pose_etat(graphql=[regle_add("389", "390", parent_actuel="167")])
    refuse = depot.lib("issue-link", "389", "390", "--parallele")
    assert refuse.returncode == 1
    assert ecritures(depot) == []
    assert mutations(depot) == []


def test_sans_le_drapeau_le_comportement_est_celui_d_avant_au_bit_pres(depot: Depot) -> None:
    """`--parallele` est ADDITIF : son absence ne pose aucun label, donc aucun lot n'est marqué."""
    depot.pose_etat(
        graphql=[
            regle_add("389", "390"),
            regle_mutation("addSubIssue", {"addSubIssue": {"subIssue": {"number": 390}}}),
        ]
    )
    acheve = depot.lib("issue-link", "389", "390")
    assert acheve.returncode == 0, acheve.stderr
    assert "lot::parallele" not in acheve.stdout
    assert ecritures(depot) == []


# =================================================================================================
# Le verdict de `startables`, figé (#393) — l'invariant qu'on ne pourra plus rejouer
# =================================================================================================
# Le critère de #393 : « `startables` rend le même verdict qu'avant sur un parent de référence ».
# « Avant », c'est le régime `checklist`, où le marqueur « (parallèle) » vivait dans le titre d'une
# ligne de checklist ; il est parti avec #395, et avec lui la possibilité de jouer la comparaison.
# Ce qui est gardé ici est donc le verdict LUI-MÊME, figé en table de vérité.
#
# LE PARENT EST DÉRIVÉ DE LA TABLE, ET NON RECOPIÉ À CÔTÉ. C'est ce qui distingue cette référence
# d'une seconde fixture à tenir d'accord avec celle de `test_collaboration.py` (dont l'en-tête
# explique pourquoi il n'y en a qu'une là-bas) : il n'y a rien à synchroniser, le ticket naît de la
# donnée. Si la règle de blocage changeait, c'est la table qu'il faudrait rouvrir — c'est-à-dire
# l'endroit où la décision est écrite.
#
# La table est celle du parent de référence du dépôt : un socle livré, deux lots marqués au milieu,
# un lot final « tests + doc » jamais marqué. C'est la forme de 42 parents sur 42.
REFERENCE = (
    # iid,  marqué,  cycle de vie,  démarrable ?
    ("201", False, "Terminé", False),
    ("202", True, "À faire", True),
    ("203", True, "À faire", True),
    ("204", False, "À faire", False),
)


def parent_de_reference(marques: bool = True) -> str:
    """Le parent de référence sous sa forme NATIVE : des lignes « lot: » et pas une ligne de corps.

    `marques=False` retire les labels `lot::parallele` sans rien changer d'autre — c'est le
    contre-exemple qui prouve le motif : sans lui, un verdict figé serait vert sur n'importe quel
    parent qui rendrait deux lots, y compris sur un parent dont plus aucun marqueur n'est lu.
    """
    return corps_ticket(
        "Découpage natif",
        "agent::qa, prio::moyenne, type::infra",
        "Le corps ne porte plus rien du découpage depuis #395.\n",
        lots=tuple(
            (iid, "x" if statut == "Terminé" else "-", "∥" if (par and marques) else "-",
             f"Lot {iid}")
            for iid, par, statut, _ in REFERENCE
        ),
    )


def etats_de_reference() -> list[dict]:
    return [regle_statuts({iid: statut for iid, _, statut, _ in REFERENCE})]


def test_le_verdict_de_startables_est_celui_d_avant_le_changement_de_support(
    depot: Depot,
) -> None:
    """Le critère de #393, gardé sous la seule forme qui lui survive.

    Deux lots marqués se prennent ensemble, le socle livré ne barre plus personne, et le lot final
    « tests + doc » reste derrière tout le monde — parce qu'il n'est PAS marqué, et qu'un lot non
    marqué est barré par tout ce qui le précède, marqueurs compris.
    """
    depot.pose_etat(issues={"389": parent_de_reference()}, graphql=etats_de_reference())
    acheve = depot.lib("startables", "389")
    assert acheve.returncode == 0, acheve.stderr
    rendus = {ligne.split("—")[0].strip().lstrip("#") for ligne in acheve.stdout.splitlines()
              if ligne.strip()}
    assert rendus == {iid for iid, _, _, ok in REFERENCE if ok}
    # Le marqueur est ANNONCÉ à qui lit, et pas seulement pris en compte : c'est ce qui permet à
    # /ticket-start de dire « celui-ci peut partir en même temps ».
    marques = [1 for _, par, _, ok in REFERENCE if par and ok]
    assert acheve.stdout.count("(parallèle)") == len(marques)


def test_le_meme_parent_sans_ses_labels_ne_rend_plus_le_meme_verdict(depot: Depot) -> None:
    """Le contre-exemple, sans lequel le verdict figé serait vert sur une question jamais posée.

    Le marqueur ne vient plus que du label : le retirer laisse un parent strictement séquentiel, où
    #202 barre #203. Un test qui n'aurait que la moitié précédente passerait aussi bien sur un code
    qui aurait cessé de lire les marqueurs.
    """
    depot.pose_etat(
        issues={"389": parent_de_reference(marques=False)}, graphql=etats_de_reference()
    )
    acheve = depot.lib("startables", "389")
    assert acheve.returncode == 0, acheve.stderr
    rendus = [ligne for ligne in acheve.stdout.splitlines() if ligne.strip()]
    assert len(rendus) == 1, acheve.stdout
    assert "#202" in rendus[0]
    assert "(parallèle)" not in acheve.stdout


def test_l_ordre_des_lots_du_parent_est_celui_que_la_forge_rend(depot: Depot) -> None:
    """L'ordre POSÉ (#391) est celui qui est RESTITUÉ, et c'est ce qui fait le plan.

    Le pendant en lecture des tests d'ordre plus haut : `subtickets` ne trie rien, il rend les lots
    dans l'ordre où les sub-issues arrivent — donc dans celui que `reprioritizeSubIssue` a posé. Le
    test le montre en donnant au double un ordre qui n'est PAS l'ordre numérique : un verbe qui
    trierait par iid rendrait la même chose sur une liste déjà croissante.
    """
    a_rebours = tuple(reversed([iid for iid, _, _, _ in REFERENCE]))
    corps = corps_ticket(
        "Découpage natif",
        "type::infra",
        "",
        lots=tuple((iid, "-", "-", f"Lot {iid}") for iid in a_rebours),
    )
    depot.pose_etat(issues={"389": corps}, graphql=etats_de_reference())
    acheve = depot.lib("subtickets", "389")
    assert acheve.returncode == 0, acheve.stderr
    assert [ligne[0] for ligne in colonnes(acheve.stdout)] == list(a_rebours)


# =================================================================================================
# Ce que le changement de support ne devait PAS toucher (#389)
# =================================================================================================


def test_le_marqueur_ne_salit_aucun_nom_de_branche(depot: Depot) -> None:
    """La raison pour laquelle le marqueur est un LABEL et pas un mot du titre.

    `gl_branch_from_raw` dérive le slug de branche du titre du ticket : un « (parallèle) » y aurait
    sali tous les noms de branches des lots marqués, pour toujours. Le label le porte à côté, d'où
    un nom de branche qui ne dit rien du découpage.
    """
    lot = corps_ticket(
        "Écran de suivi",
        "type::feature, lot::parallele",
        "Sous-ticket de #389.\n",
        parent="389",
    )
    depot.pose_etat(issues={"202": lot})
    acheve = depot.lib("branch-for", "202")
    assert acheve.returncode == 0, acheve.stderr
    branche = acheve.stdout.strip()
    assert branche == "feat/202-ecran-de-suivi"
    assert "parallele" not in branche


def test_un_parent_sans_sub_issue_n_est_pas_un_parent(depot: Depot) -> None:
    """Le principe « le régime décide, jamais la présence » devenu sans objet (#395).

    Il gardait, pendant la migration, contre la tentation de lire « nativement si des sub-issues
    existent, dans la prose sinon ». Avec un support unique la question ne peut plus se poser, et le
    message le dit dans les mots du support qui reste : envoyer quelqu'un chercher une section
    « ## Sous-tickets » serait le faire corriger un support disparu.
    """
    orphelin = corps_ticket("Ticket ordinaire", "type::infra", "Aucun découpage ici.\n")
    depot.pose_etat(issues={"500": orphelin})
    acheve = depot.lib("subtickets", "500")
    assert acheve.returncode == 1
    assert "pas un ticket parent" in acheve.stderr
    assert "Sous-tickets" not in acheve.stderr
