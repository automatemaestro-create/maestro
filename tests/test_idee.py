"""`/idee` et les trois verbes qui ordonnent la file (#1013, docs/10 §5.2).

Une idée exposée en conversation se transforme en tickets **et** fait sa place dans la file. Deux
ordres décident de ce qui se traite en premier, et aucun n'est une liste tenue à part :

- **entre jalons, l'échéance** — `current-milestone` trie par `DUE_DATE` : la date EST l'ordre ;
- **dans un jalon, `prio::`** — `queue.sh` trie par priorité puis par iid.

`/idee` les écrit par trois verbes de `lib.sh` — `milestone-echeance`, `milestone-cree`,
`prio-pose` —, parce que les écritures de forge sont interdites sous `.claude/commands/**`. Cette
suite garde les verbes sur le dépôt jetable du harnais partagé (ni réseau ni compte), puis les
règles du prompt qui décident de ce que la commande fait seule et de ce qu'elle demande.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    GIT,
    RACINE,
    Depot,
    corps_ticket,
    ecritures,
    jalon,
    monte_depot,
)

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

LIB = RACINE / "scripts" / "gitlab" / "lib.sh"
IDEE = RACINE / ".claude" / "commands" / "idee.md"
DOCS_10 = RACINE / "docs" / "10-workflow-git.md"
CLAUDE_MD = RACINE / "CLAUDE.md"

JALON = "Continuité & multi-projet"
AUTRE = "Outillage de la forge"


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


@pytest.fixture
def file(tmp_path: Path) -> Depot:
    """Deux jalons actifs : l'un échu au 2027-11-09, l'autre (d'outillage) sans échéance."""
    depot = monte_depot(tmp_path)
    depot.pose_etat(jalons=[
        jalon(JALON, "", ouverts=3, numero=21, echeance="2027-11-09"),
        jalon(AUTRE, "rail: outillage\n", ouverts=2, numero=17),
    ])
    return depot


def jalon_du_double(depot: Depot, titre: str) -> dict:
    """Le jalon tel que l'état du double le porte APRÈS d'éventuelles écritures."""
    etat = json.loads(depot.etat_json.read_text(encoding="utf-8"))
    for entree in etat.get("jalons", []):
        if entree["title"] == titre:
            return entree
    raise AssertionError(f"aucun jalon « {titre} » dans l'état du double")


def prose(chemin: Path) -> str:
    """Le texte d'un fichier, blancs repliés — pour asserter sur une phrase, pas sur sa coupe."""
    return re.sub(r"\s+", " ", chemin.read_text(encoding="utf-8"))


def blocs_de_code(chemin: Path) -> list[str]:
    """Les blocs de code d'un prompt — c'est là que vit une commande QU'IL JOUE."""
    texte = chemin.read_text(encoding="utf-8")
    return re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", texte, re.M | re.S)


# =================================================================================================
# milestone-echeance — lire ou poser la place d'un jalon dans la file
# =================================================================================================


def test_l_echeance_se_lit_puis_se_pose_et_se_relit(file: Depot) -> None:
    """L'aller-retour : c'est cette relecture que `current-milestone` trie."""
    lue = file.lib("milestone-echeance", JALON)
    assert lue.returncode == 0, lue.stderr
    assert lue.stdout == "2027-11-09\n"

    posee = file.lib("milestone-echeance", JALON, "2027-10-01")
    assert posee.returncode == 0, posee.stderr
    assert "2027-11-09 → 2027-10-01" in posee.stdout
    # Minuit UTC, comme les échéances déjà posées sur le dépôt.
    assert jalon_du_double(file, JALON)["due_on"] == "2027-10-01T00:00:00Z"
    assert file.lib("milestone-echeance", JALON).stdout == "2027-10-01\n"


def test_reposer_la_meme_echeance_n_ecrit_rien(file: Depot) -> None:
    rejoue = file.lib("milestone-echeance", JALON, "2027-11-09")
    assert rejoue.returncode == 0, rejoue.stderr
    assert "rien à écrire" in rejoue.stdout
    assert not ecritures(file)


def test_un_jalon_sans_echeance_est_une_abstention_muette(file: Depot) -> None:
    """Code 3, rien sur stdout — il se range dernier de son rail : une place, pas une panne."""
    absente = file.lib("milestone-echeance", AUTRE)
    assert absente.returncode == 3
    assert absente.stdout == ""
    # Contre-exemple : le verbe sait parler, sans quoi le silence ne prouverait rien.
    file.lib("milestone-echeance", AUTRE, "2027-09-15")
    assert file.lib("milestone-echeance", AUTRE).stdout == "2027-09-15\n"


def test_un_jalon_inconnu_est_nomme(file: Depot) -> None:
    inconnu = file.lib("milestone-echeance", "Phase 42")
    assert inconnu.returncode == 1
    assert "aucun jalon intitulé « Phase 42 »" in inconnu.stderr


@pytest.mark.parametrize("date", ["2027-02-30", "2027-9-1", "demain", "01/10/2027"])
def test_une_date_invalide_est_refusee_avant_la_forge(depot: Depot, date: str) -> None:
    """Le refus gratuit tombe avant tout aller : rien n'est demandé à personne."""
    refus = depot.lib("milestone-echeance", JALON, date)
    assert refus.returncode == 2
    assert "AAAA-MM-JJ" in refus.stderr
    assert not depot.appels(), "une date mal formée se voit sans la forge"


# =================================================================================================
# milestone-cree — un jalon naît avec sa place
# =================================================================================================


def test_un_jalon_produit_nait_avec_son_echeance_et_sans_marqueur(file: Depot) -> None:
    cree = file.lib("milestone-cree", "Projet d'abord", "2027-10-15")
    assert cree.returncode == 0, cree.stderr
    assert "échéance 2027-10-15 (relue), rail produit" in cree.stdout
    neuf = jalon_du_double(file, "Projet d'abord")
    assert neuf["due_on"] == "2027-10-15T00:00:00Z"
    assert neuf["description"] == "", "« produit » est l'absence du marqueur"
    assert file.lib("milestone-echeance", "Projet d'abord").stdout == "2027-10-15\n"


def test_un_jalon_d_outillage_porte_son_marqueur_en_tete(file: Depot) -> None:
    cree = file.lib("milestone-cree", "Forge v2", "2027-12-01", "outillage")
    assert cree.returncode == 0, cree.stderr
    assert jalon_du_double(file, "Forge v2")["description"] == "rail: outillage"


# ─── #1018 : GitHub enregistre la veille d'une échéance envoyée à la création ────────────────────


def test_le_double_enregistre_la_veille_d_une_echeance_postee(file: Depot) -> None:
    """L'échantillon fautif : la forme de #1013 — l'échéance DANS le POST — rend la veille.

    Sans cette moitié, le double serait fidèle à ce qu'on lui envoie, et la suite resterait verte
    sur le verbe qui a rangé le jalon n° 21 un jour trop tôt (2026-09-19).
    """
    (file.racine / "essai-post.sh").write_text(
        'gh api --method POST "repos/equipe-test/maestro/milestones" --raw-field title="Neuf" '
        '--raw-field due_on="2028-01-05T00:00:00Z" >/dev/null\n',
        encoding="utf-8",
        newline="\n",
    )
    assert file._bash("essai-post.sh", cwd=None).returncode == 0
    assert jalon_du_double(file, "Neuf")["due_on"] == "2028-01-04T00:00:00Z"


def test_l_echeance_ne_voyage_pas_dans_le_post_et_se_pose_par_patch(file: Depot) -> None:
    """La correction : le jalon naît sans échéance, le PATCH la pose, puis la fiche est relue."""
    cree = file.lib("milestone-cree", "L'équipe sur mesure", "2028-01-05")
    assert cree.returncode == 0, cree.stderr

    ecrites = [ligne for ligne in ecritures(file) if "/milestones" in ligne]
    creation, pose = ecrites
    assert "--method\tPOST" in creation and "due_on" not in creation
    assert "--method\tPATCH" in pose and "due_on=2028-01-05T00:00:00Z" in pose
    assert jalon_du_double(file, "L'équipe sur mesure")["due_on"] == "2028-01-05T00:00:00Z"


def test_un_titre_deja_pris_est_un_refus_et_rien_n_est_cree(file: Depot) -> None:
    """Pas un succès idempotent : le jalon qui le porte peut être un autre, fermé, d'une phase
    passée — « déjà là » y rangerait des tickets sans que personne l'ait vu."""
    refus = file.lib("milestone-cree", AUTRE, "2027-12-01", "outillage")
    assert refus.returncode == 4
    assert "déjà « Outillage de la forge » (n° 17)" in refus.stderr
    assert "milestone-echeance" in refus.stderr, "le geste d'après est nommé"
    assert not ecritures(file)


@pytest.mark.parametrize(
    "arguments",
    [("Sans date",), ("Date fausse", "2027-13-01"), ("Rail faux", "2027-12-01", "moteur")],
)
def test_la_creation_refuse_avant_la_forge(depot: Depot, arguments: tuple[str, ...]) -> None:
    """L'échéance est obligatoire : un jalon sans date se range dernier, personne ne l'y a mis."""
    refus = depot.lib("milestone-cree", *arguments)
    assert refus.returncode == 2
    assert not depot.appels()


# =================================================================================================
# prio-pose — l'ordre dans un jalon
# =================================================================================================


def pose_ticket(depot: Depot, labels: str, iid: str = "7") -> None:
    depot.pose_etat(issues={iid: corps_ticket("Un ticket", labels, "Corps.")})


def ecritures_de_labels(depot: Depot) -> list[str]:
    return [ligne for ligne in ecritures(depot) if "/labels" in ligne]


def test_la_priorite_est_remplacee_et_l_ajout_precede_le_retrait(depot: Depot) -> None:
    """Une panne entre les deux laisse deux priorités, visibles — jamais un ticket sans priorité."""
    pose_ticket(depot, "type::feature, prio::moyenne")
    posee = depot.lib("prio-pose", "7", "haute")
    assert posee.returncode == 0, posee.stderr
    assert posee.stdout == "#7 : prio::moyenne → prio::haute.\n"

    ajout, retrait = ecritures_de_labels(depot)
    assert "-X\tPOST" in ajout and "labels[]=prio::haute" in ajout
    assert "-X\tDELETE" in retrait and retrait.endswith("issues/7/labels/prio%3A%3Amoyenne")


def test_la_meme_priorite_n_ecrit_rien(depot: Depot) -> None:
    pose_ticket(depot, "prio::haute, type::infra")
    rejoue = depot.lib("prio-pose", "7", "haute")
    assert rejoue.returncode == 0
    assert "déjà prio::haute" in rejoue.stdout
    assert not ecritures(depot)


def test_deux_priorites_sont_reparees_sans_reposer_la_bonne(depot: Depot) -> None:
    """Un ticket qui en porte deux est justement celui qu'il faut réparer : les autres partent."""
    pose_ticket(depot, "prio::basse, prio::haute")
    repare = depot.lib("prio-pose", "7", "haute")
    assert repare.returncode == 0, repare.stderr
    (seule,) = ecritures_de_labels(depot)
    assert "-X\tDELETE" in seule and seule.endswith("prio%3A%3Abasse")


def test_un_ticket_sans_priorite_la_recoit(depot: Depot) -> None:
    pose_ticket(depot, "type::bug")
    posee = depot.lib("prio-pose", "7", "basse")
    assert posee.stdout == "#7 : aucune priorité → prio::basse.\n"
    (seule,) = ecritures_de_labels(depot)
    assert "labels[]=prio::basse" in seule


def test_un_niveau_inconnu_est_refuse_avant_la_forge(depot: Depot) -> None:
    refus = depot.lib("prio-pose", "7", "urgente")
    assert refus.returncode == 2
    assert "haute | moyenne | basse" in refus.stderr
    assert not depot.appels()


# =================================================================================================
# Le double rejoue la fiche d'un jalon : ce qu'il ne peut pas garder lui-même
# =================================================================================================


def test_le_double_rejoue_le_programme_de_la_fiche() -> None:
    """Le `gh` factice n'exécute pas jq : il refait la fiche en Python.

    Toute la suite serait donc verte sur une sélection PÉRIMÉE si le verbe changeait la sienne. Ce
    contrôle épingle le programme que `jalons_rest` reproduit, à l'endroit où il vit.
    """
    texte = LIB.read_text(encoding="utf-8")
    assert (
        "GL_MS_FICHE_JQ='map(select(.title == env.GL_MS_TITRE)) | if length == 0 then empty else "
        '"\\(.[0].number)\\t\\((.[0].due_on // "-")[0:10])\\t\\(.[0].state)" end\''
    ) in texte, "le programme de la fiche a bougé — `jalons_rest` rejoue encore l'ancien"
    assert "milestones?state=all&per_page=100" in texte, "la fiche lit les jalons fermés compris"


# =================================================================================================
# Le prompt de `/idee`
# =================================================================================================


GH_EN_ECRITURE = re.compile(
    r"gh\s+api\s+(?:-X|--method)\s+(?:POST|PATCH|PUT|DELETE)"
    r"|gh\s+(?:issue\s+(?:edit|create|close)|pr\s+(?:merge|edit|close)|label\s+create)\b"
)


def test_le_motif_d_ecriture_sait_dire_oui_et_non() -> None:
    """L'échantillon fautif avant le balayage — un motif creux balaierait sans rien voir."""
    for fautif in (
        'gh issue create --title "x" --body-file f.md',
        "gh api --method PATCH repos/o/r/milestones/17 --raw-field due_on=2027-01-01",
        "gh api -X POST repos/o/r/issues/7/labels -f labels[]=prio::haute",
    ):
        assert GH_EN_ECRITURE.search(fautif), fautif
    for licite in (
        'gh issue list --state all --search "projet in:title,body" --limit 20',
        "bash scripts/gitlab/lib.sh prio-pose 7 haute",
    ):
        assert not GH_EN_ECRITURE.search(licite), licite


def test_la_commande_n_ecrit_jamais_la_forge_a_la_main() -> None:
    """Tickets par `/ticket-create`, jalons et priorités par les verbes — rien d'autre."""
    fautifs = [bloc for bloc in blocs_de_code(IDEE) if GH_EN_ECRITURE.search(bloc)]
    assert not fautifs, f"/idee écrit la forge hors des verbes : {fautifs}"
    texte = prose(IDEE)
    assert "par le skill **`/ticket-create`** et jamais par un `gh issue create` recopié" in texte


def test_la_commande_joue_les_trois_verbes_de_la_file() -> None:
    joues = "\n".join(blocs_de_code(IDEE))
    for verbe in ("milestone-cree", "milestone-echeance", "prio-pose"):
        assert f"bash scripts/gitlab/lib.sh {verbe} " in joues, f"/idee ne joue pas `{verbe}`"


def test_ce_que_la_commande_tranche_seule_et_ce_qu_elle_demande() -> None:
    """Ni silencieuse, ni dépendante : la demande de #1013, écrite en règle."""
    texte = prose(IDEE)
    assert "Tu arbitres seul ce qui ne demande pas d'humain, et tu le dis." in texte
    assert "Tu demandes ce qui en demande — et seulement ça" in texte
    assert "**vraie pause** : rien ne s'écrit côté forge avant la réponse" in texte
    assert "Rien ne naît en double." in texte
    assert "Aucun lexique pour juger" in texte


def test_la_commande_n_abandonne_rien_et_ne_ferme_aucun_jalon() -> None:
    texte = prose(IDEE)
    assert "`/ticket-abandon` est une décision humaine" in texte
    assert "Tu ne **fermes** ni ne **renommes** aucun jalon" in texte
    assert not re.search(r"^\s*/ticket-abandon", IDEE.read_text(encoding="utf-8"), re.M)


def test_la_roadmap_passe_par_un_ticket_repris_par_ticket_start() -> None:
    """Ce qui empêche l'idée de se perdre : l'analyse dans la forge, puis la doc par un ticket."""
    texte = prose(IDEE)
    assert "ticket `type::doc`" in texte
    assert "dont le corps **est l'analyse**" in texte
    assert "**passe la main à `/ticket-start <iid>`**" in texte
    assert "`docs/NN-decision-<slug>.md`" in texte


def test_docs_claude_md_et_l_usage_nomment_les_verbes() -> None:
    docs = prose(DOCS_10)
    claude = prose(CLAUDE_MD)
    usage = LIB.read_text(encoding="utf-8")
    assert "### 5.2 D'une idée au backlog ordonné — `/idee` (#1013)" in docs
    for verbe in ("milestone-echeance", "milestone-cree", "prio-pose"):
        assert verbe in docs and verbe in claude, verbe
        assert f'echo "  {verbe} ' in usage, f"`{verbe}` absent de l'usage de lib.sh"
    assert "`/idee` ⊇ `/ticket-create`" in docs, "le chaînage est déclaré en docs/10 §7.1"
