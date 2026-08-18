"""Tests du cycle de vie porté par les labels `workflow::*` (ticket #212, chantier #207).

**L'invariant couvert ici est celui qu'aucune forge n'assure à notre place.** Le cycle de vie était
porté par le champ Status natif de GitLab, où « un seul statut à la fois » était une garantie du
produit. Depuis #207 il est porté par une famille de labels scopés — et un label, ici comme
ailleurs, ne s'exclut pas tout seul : rien n'empêche un ticket de porter `workflow::a-faire` **et**
`workflow::en-revue`. L'exclusion mutuelle est donc à la charge de l'outillage —

    toute pose AJOUTE la cible et RETIRE les cinq autres dans le MÊME appel

— et c'est la régression la plus probable du dispositif : une pose additive passerait tous les
tests de bout en bout (le ticket *porte* bien son nouvel état), ne se verrait pas à l'œil nu sur une
ligne de backlog (les lectures rendent le premier label rencontré) et ne se manifesterait que plus
tard, en tickets à deux états.

**Ce que le passage à GitHub a changé, et ce qu'il n'a pas changé** (#344). Côté GitLab la pose
était une mutation GraphQL portant deux listes de GID (`addLabelIds` / `removeLabelIds`), et
l'invariant se lisait dans leur contenu. Côté GitHub, `PATCH /issues/:n` **remplace** l'ensemble des
labels : « poser la cible » et « retirer les cinq autres » ne sont plus deux gestes qu'on prend soin
de grouper, mais un seul geste indivisible — et c'est l'ensemble final qui est vérifié ici. La
régression a changé de forme, pas de nature : elle serait maintenant un ensemble final qui garde un
`workflow::` de trop, ou qui perd un `type::`/`agent::`/`prio::` au passage.

Deux helpers posent le cycle de vie, et les deux sont vérifiés ici : `set-workflow` (toutes les
commandes) et `begin` (le démarrage groupé de `/ticket-start`, où la pose voyage avec l'assignation
— un endroit facile à oublier).

Même parti pris que [`test_collaboration.py`](test_collaboration.py) : un **dépôt jetable** dans
`tmp_path`, le VRAI `lib.sh`, et un `gh` factice en tête du `PATH` qui journalise les écritures
reçues. **Ni réseau, ni compte de forge, ni écriture** dans le dépôt de travail.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")

DEPOT = "equipe-test/maestro"

#: Les six états, dans l'ordre du flux. Le test ne connaît QUE cette table : si un état était
#: ajouté ou renommé sans que `lib.sh` suive, l'assertion « les cinq autres sont retirés » tombe.
SLUGS = ("a-faire", "en-cours", "en-revue", "termine", "abandonne", "doublon")

#: Libellé (surface) -> slug (stockage), le contrat documenté en tête de `lib.sh`.
LIBELLES = {
    "À faire": "a-faire",
    "En cours": "en-cours",
    "En revue": "en-revue",
    "Terminé": "termine",
    "Abandonné": "abandonne",
    "Doublon": "doublon",
}

# --- Le gh factice --------------------------------------------------------------------------------
# Piloté par $MAESTRO_FAUX_GH (état JSON) et $MAESTRO_FAUX_GH_ECRITURES (une écriture par ligne).
# Il ne connaît que ce dont ce module a besoin : lire les labels du dépôt et d'un ticket, lister un
# backlog, et accuser réception d'un `PATCH /issues/:n`.
FAUX_GH = r'''
import json
import os
import sys

with open(os.environ["MAESTRO_FAUX_GH"], encoding="utf-8") as f:
    etat = json.load(f)

args = sys.argv[1:]


def sortie(texte="", code=0):
    # Écriture en octets : sous Windows, sys.stdout encoderait en cp1252 et rendrait du mojibake
    # là où l'API renvoie de l'UTF-8 (« À faire », « Terminé »…). Piège de #141.
    sys.stdout.buffer.write(texte.encode("utf-8"))
    sys.stdout.buffer.flush()
    raise SystemExit(code)


def compact(obj):
    # L'outillage parse en grep/awk sur le JSON BRUT : pas d'espaces, pas d'échappement des
    # non-ASCII, sinon les motifs de lib.sh ne matchent plus.
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"


def noms(liste):
    return {"nodes": [{"name": n} for n in liste]}


if args[:2] == ["auth", "status"]:
    sortie(code=0)

if args[:2] == ["api", "user"]:
    sortie(compact({"login": etat["moi"], "id": 4242}))

# --- Écriture : PATCH /repos/<dépôt>/issues/<n> ---------------------------------------------------
if args[:1] == ["api"] and "-X" in args and args[args.index("-X") + 1] == "PATCH":
    with open(os.environ["MAESTRO_FAUX_GH_ECRITURES"], "a", encoding="utf-8") as f:
        f.write(json.dumps(args, ensure_ascii=False) + "\n")
    if etat.get("ecriture_en_echec"):
        sortie(compact({"message": "refus simulé"}), code=1)
    sortie(compact({"number": 212}))

if args[:2] == ["api", "graphql"]:
    requete = "".join(a[len("query="):] for a in args[2:] if a.startswith("query="))

    # Backlog (gh_backlog) : ce que balaie `reconcile-workflow` sans argument.
    if "issues(first:" in requete:
        etats = "CLOSED" if "CLOSED" in requete and "OPEN" not in requete else "OPEN"
        entrees = etat.get("backlog_closed", []) if etats == "CLOSED" else []
        sortie(compact({"data": {"repository": {"issues": {"nodes": [
            {
                "number": int(e["iid"]),
                "title": e.get("titre", "ticket %s" % e["iid"]),
                "labels": noms(e.get("labels", [])),
                "assignees": {"nodes": []},
            }
            for e in entrees
        ]}}}}))

    # Labels du dépôt + labels du ticket, en UNE lecture (gh_labels_du_scope_et_du_ticket).
    if "labels(first:100" in requete and "issue(number:" in requete:
        iid = requete.split("issue(number:", 1)[1].split(")", 1)[0]
        if iid in etat.get("iids_illisibles", []):
            sortie(compact({"data": {"repository": {
                "labels": noms(etat["labels"]), "issue": None}}}))
        portes = etat.get("labels_par_iid", {}).get(iid, etat.get("labels_ticket", []))
        sortie(compact({"data": {"repository": {
            "labels": noms(etat["labels"]),
            "issue": {"number": int(iid), "labels": noms(portes)},
        }}}))

    # Labels du dépôt seuls (gh_workflow_gids).
    if "labels(first:100" in requete:
        sortie(compact({"data": {"repository": {"labels": noms(etat["labels"])}}}))

    # Labels + assignés d'un ticket (gh_issue_owner), sur quoi s'appuie `reconcile-workflow <iid>`.
    if "issue(number:" in requete and "assignees(first:" in requete:
        iid = requete.split("issue(number:", 1)[1].split(")", 1)[0]
        if iid in etat.get("iids_illisibles", []):
            sortie(compact({"data": {"repository": {"issue": None}}}))
        portes = etat.get("labels_par_iid", {}).get(iid, etat.get("labels_ticket", []))
        sortie(compact({"data": {"repository": {"issue": {
            "labels": noms(portes), "assignees": {"nodes": []}}}}}))

    # Commentaires du ticket : le suivi maison (dates, temps passé) que `begin` pose après coup.
    if "comments(" in requete:
        sortie(compact({"data": {"repository": {"issue": {
            "comments": {"nodes": []}}}}}))

    sortie(code=1)

sortie(code=1)
'''


@dataclass
class Depot:
    """Dépôt jetable équipé du vrai `lib.sh` et d'un `gh` factice."""

    racine: Path
    fauxbin: Path
    etat_json: Path
    ecritures_log: Path
    etat: dict

    def pose_etat(self, **entrees: object) -> None:
        self.etat.update(entrees)
        self.etat_json.write_text(
            json.dumps(self.etat, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )

    def ecritures(self) -> list[list[str]]:
        """Les appels d'écriture reçus (argv de chaque `gh api -X PATCH`), vide si aucun."""
        if not self.ecritures_log.exists():
            return []
        lignes = self.ecritures_log.read_text(encoding="utf-8").splitlines()
        return [json.loads(ligne) for ligne in lignes if ligne]

    def lib(self, *args: str) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        environnement.update(
            {
                "HOME": str(self.racine.parent / "home"),
                "PATH": os.pathsep.join([str(self.fauxbin), environnement.get("PATH", "")]),
                "MAESTRO_GITHUB_REPO": DEPOT,
                # Le retry ne sert qu'aux hoquets réseau : une réponse volontairement muette ne
                # doit pas coûter trois secondes au test.
                "GL_GQL_RETRIES": "1",
                "GL_GQL_RETRY_DELAY": "0",
                "MAESTRO_FAUX_GH": str(self.etat_json),
                "MAESTRO_FAUX_GH_ECRITURES": str(self.ecritures_log),
            }
        )
        assert BASH is not None
        return subprocess.run(  # noqa: S603
            [BASH, str(self.racine / "scripts/gitlab/lib.sh"), *args],
            cwd=str(self.racine),
            env=environnement,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    racine = tmp_path / "clone"
    fauxbin = tmp_path / "fauxbin"
    for dossier in (racine, fauxbin, tmp_path / "home"):
        dossier.mkdir()

    cible = racine / "scripts/gitlab/lib.sh"
    cible.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RACINE / "scripts/gitlab/lib.sh", cible)

    # Le gh factice : un script Python derrière un lanceur nommé `gh` (sans extension), pour que
    # le `command -v gh` de lib.sh le trouve comme le vrai.
    (fauxbin / "faux_gh.py").write_text(FAUX_GH, encoding="utf-8", newline="\n")
    lanceur = fauxbin / "gh"
    interpreteur = sys.executable.replace(chr(92), "/")
    lanceur.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{interpreteur}" "{(fauxbin / "faux_gh.py").as_posix()}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    lanceur.chmod(0o755)

    depot = Depot(
        racine=racine,
        fauxbin=fauxbin,
        etat_json=tmp_path / "faux-gh.json",
        ecritures_log=tmp_path / "ecritures.log",
        etat={},
    )
    depot.pose_etat(
        moi="MaestroAgents",
        labels=[f"workflow::{s}" for s in SLUGS] + ["type::infra", "prio::moyenne"],
        labels_ticket=["type::infra", "prio::moyenne", "workflow::a-faire"],
    )
    return depot


# --- Lecture d'une écriture -----------------------------------------------------------------------


def _labels_poses(argv: list[str]) -> list[str]:
    """Les labels de l'ensemble final envoyé par `PATCH /issues/:n`, dans l'ordre reçu."""
    return [a[len("labels[]="):] for a in argv if a.startswith("labels[]=")]


def _assignes_poses(argv: list[str]) -> list[str]:
    return [a[len("assignees[]="):] for a in argv if a.startswith("assignees[]=")]


def _ecriture_unique(depot: Depot) -> list[str]:
    """La seule écriture attendue — le « dans le MÊME appel » de l'invariant est ici."""
    ecritures = depot.ecritures()
    assert len(ecritures) == 1, f"une seule écriture attendue, reçu {len(ecritures)} : {ecritures}"
    return ecritures[0]


# =================================================================================================
# L'invariant : poser un état retire les cinq autres
# =================================================================================================


@pytest.mark.parametrize(("libelle", "slug"), sorted(LIBELLES.items()))
def test_set_workflow_ajoute_la_cible_et_retire_les_cinq_autres(
    depot: Depot, libelle: str, slug: str
) -> None:
    """Pour CHACUN des six états : l'ensemble final porte la cible, et aucun autre `workflow::`.

    Paramétré sur les six plutôt que sur un cas représentatif : une table de correspondance se
    complète à la main (six `case` dans `gl_workflow_slug`), et c'est exactement le genre d'endroit
    où un état s'oublie — il faut que le test tombe sur celui-là, pas sur « un état ».
    """
    depot.pose_etat(
        labels_ticket=["type::infra", "prio::moyenne"] + [f"workflow::{s}" for s in SLUGS]
    )
    acheve = depot.lib("set-workflow", "212", libelle)
    assert acheve.returncode == 0, acheve.stderr

    poses = _labels_poses(_ecriture_unique(depot))
    workflow = [nom for nom in poses if nom.startswith("workflow::")]
    assert workflow == [f"workflow::{slug}"]


def test_set_workflow_vise_le_bon_ticket(depot: Depot) -> None:
    """La cible de l'écriture est le chemin du ticket, jamais un identifiant deviné."""
    assert depot.lib("set-workflow", "212", "En revue").returncode == 0
    assert f"repos/{DEPOT}/issues/212" in _ecriture_unique(depot)


def test_set_workflow_accepte_le_slug_comme_le_libelle(depot: Depot) -> None:
    """Contrat d'entrée : « en-cours » ≡ « En cours ». Les deux produisent la MÊME écriture."""
    assert depot.lib("set-workflow", "212", "En cours").returncode == 0
    par_libelle = _ecriture_unique(depot)

    depot.ecritures_log.unlink()
    assert depot.lib("set-workflow", "212", "en-cours").returncode == 0
    assert _ecriture_unique(depot) == par_libelle


def test_set_workflow_rend_le_libelle_jamais_le_slug(depot: Depot) -> None:
    """Contrat de sortie : le slug est un détail de stockage, il ne sort pas de `lib.sh`."""
    acheve = depot.lib("set-workflow", "212", "termine")
    assert acheve.returncode == 0, acheve.stderr
    assert "Terminé" in acheve.stdout
    assert "termine" not in acheve.stdout


def test_set_workflow_retire_aussi_un_workflow_exotique(depot: Depot) -> None:
    """Le filtre porte sur le SCOPE, pas sur les six slugs connus.

    Un `workflow::` posé à la main depuis l'UI doit partir lui aussi : sinon la dérive que
    `doctor.sh` signale (0 ou ≥ 2 labels du scope) survivrait à la pose censée la réparer.
    """
    depot.pose_etat(labels_ticket=["type::infra", "workflow::a-trier", "workflow::a-faire"])
    assert depot.lib("set-workflow", "212", "En cours").returncode == 0
    poses = _labels_poses(_ecriture_unique(depot))
    assert [nom for nom in poses if nom.startswith("workflow::")] == ["workflow::en-cours"]


def test_set_workflow_ne_touche_pas_aux_labels_de_categorisation(depot: Depot) -> None:
    """`type::`/`agent::`/`prio::` sont RÉÉCRITS à l'identique : l'ensemble final les préserve.

    C'est le prix du `PATCH` qui remplace tout — et le risque qui va avec : une lecture préalable
    ratée ne se verrait pas comme une erreur, mais comme un ticket qui perd sa catégorisation.
    """
    depot.pose_etat(
        labels_ticket=["type::infra", "agent::devops", "prio::haute", "workflow::a-faire"]
    )
    assert depot.lib("set-workflow", "212", "En cours").returncode == 0
    poses = _labels_poses(_ecriture_unique(depot))
    assert sorted(poses) == sorted(
        ["type::infra", "agent::devops", "prio::haute", "workflow::en-cours"]
    )


# =================================================================================================
# `begin` — la même exclusion, dans le démarrage groupé de /ticket-start
# =================================================================================================


def test_begin_retire_aussi_les_cinq_autres(depot: Depot) -> None:
    """Le démarrage groupé pose « En cours » : l'exclusion doit y être, pas seulement dans
    `set-workflow`.

    C'est le point d'oubli naturel du dispositif : la pose y est noyée dans un appel qui porte
    aussi l'assignation, écrit à part de `gl_set_workflow`.
    """
    depot.pose_etat(
        labels_ticket=["type::infra", "prio::moyenne"] + [f"workflow::{s}" for s in SLUGS]
    )
    acheve = depot.lib("begin", "212")
    assert acheve.returncode == 0, acheve.stderr

    poses = _labels_poses(depot.ecritures()[0])
    assert [nom for nom in poses if nom.startswith("workflow::")] == ["workflow::en-cours"]


def test_begin_groupe_labels_et_assignation_dans_un_seul_appel(depot: Depot) -> None:
    """Cycle de vie et assignation voyagent ensemble — un seul aller-retour réseau.

    Les DATES, elles, partent ensuite : le suivi maison vit dans un commentaire (docs/27 §5), donc
    dans un autre objet. Leur échec ne défait pas le démarrage, et c'est voulu — le ticket est
    pris, ce qui est l'enjeu de l'anti-collision.
    """
    assert depot.lib("begin", "212").returncode == 0
    premiere = depot.ecritures()[0]
    assert "workflow::en-cours" in _labels_poses(premiere)
    assert _assignes_poses(premiere) == ["MaestroAgents"]


# =================================================================================================
# Refus : ce qui ne doit RIEN écrire
# =================================================================================================


def test_set_workflow_refuse_une_valeur_inconnue_sans_rien_ecrire(depot: Depot) -> None:
    """Une valeur hors vocabulaire s'arrête avant l'écriture, en listant les six attendues.

    Sans ce refus, une faute de frappe (« en revu ») poserait un septième label hors scope — ou,
    pire, retirerait les six sans rien remettre.
    """
    acheve = depot.lib("set-workflow", "212", "en revu")
    assert acheve.returncode != 0
    assert "inconnue" in acheve.stderr
    for libelle in LIBELLES:
        assert libelle in acheve.stderr
    assert depot.ecritures() == [], "aucune écriture ne doit partir sur une valeur inconnue"


def test_set_workflow_refuse_si_les_labels_ne_sont_pas_provisionnes(depot: Depot) -> None:
    """Dépôt sans `workflow::*` : on s'arrête en renvoyant vers `bootstrap.sh`.

    Poser la cible sans pouvoir retirer les autres serait précisément la pose partielle que tout
    ce module cherche à empêcher — mieux vaut ne rien écrire.
    """
    depot.pose_etat(labels=["type::infra"])
    acheve = depot.lib("set-workflow", "212", "En cours")
    assert acheve.returncode != 0
    assert "provisionner" in acheve.stderr.lower()
    assert depot.ecritures() == []


def test_set_workflow_signale_un_refus_de_la_forge(depot: Depot) -> None:
    """Une écriture refusée par l'API est un échec — pas un succès silencieux."""
    depot.pose_etat(ecriture_en_echec=True)
    acheve = depot.lib("set-workflow", "212", "En cours")
    assert acheve.returncode != 0
    assert "Échec" in acheve.stderr


# =================================================================================================
# Réconciliation : « Terminé » posé sur ce que le merge a soldé (#275)
# =================================================================================================
# Le merge FERME le ticket mais ne touche à aucun label : depuis #207 seul `/branch-cleanup` — un
# geste manuel — posait « Terminé », donc un ticket mergé s'affichait « En revue » indéfiniment.
# `reconcile-workflow` répare ça, et son seul vrai piège est le refus d'écraser un état final :
# `worktree-done` rend « fini » pour un ticket ABANDONNÉ exactement comme pour un ticket livré, si
# bien qu'une réconciliation naïve transformerait « Abandonné » en « Terminé » — elle réparerait une
# dérive en en créant une autre, silencieusement et sans retour possible.


def test_reconcile_pose_termine_sur_un_ticket_cible_reste_actif(depot: Depot) -> None:
    """Le cas nominal du ramassage : un ticket « En revue » soldé passe à « Terminé »."""
    depot.pose_etat(labels_ticket=["type::infra", "workflow::en-revue"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode == 0, acheve.stderr
    poses = _labels_poses(_ecriture_unique(depot))
    # L'invariant de tout ce module vaut aussi ici : la pose passe par `set-workflow`, donc les cinq
    # autres partent dans le même appel. Une réconciliation qui écrirait son propre ensemble
    # laisserait le ticket à deux états.
    assert [nom for nom in poses if nom.startswith("workflow::")] == ["workflow::termine"]


@pytest.mark.parametrize("final", ["abandonne", "doublon"])
def test_reconcile_n_ecrase_jamais_un_etat_final(depot: Depot, final: str) -> None:
    """Un ticket « Abandonné »/« Doublon » est fermé lui aussi — et ne devient JAMAIS « Terminé ».

    C'est la seule règle de cette fonction qu'on ne peut pas rattraper après coup : le label
    d'origine est perdu par la pose, et rien dans le ticket ne dirait qu'il a été abandonné.
    """
    depot.pose_etat(labels_ticket=["type::infra", f"workflow::{final}"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode == 0, acheve.stderr
    assert depot.ecritures() == [], f"« {final} » a été écrasé"


def test_reconcile_saute_un_ticket_deja_termine(depot: Depot) -> None:
    """Déjà « Terminé » : rien à écrire. C'est le cas nominal en régime établi, à chaque `gc`."""
    depot.pose_etat(labels_ticket=["type::infra", "workflow::termine"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode == 0, acheve.stderr
    assert depot.ecritures() == []


def test_reconcile_pose_sur_un_ticket_sans_aucun_cycle_de_vie(depot: Depot) -> None:
    """Zéro label `workflow::` (ticket créé depuis l'UI web) : soldé, donc « Terminé »."""
    depot.pose_etat(labels_ticket=["type::infra"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode == 0, acheve.stderr
    poses = _labels_poses(_ecriture_unique(depot))
    assert [nom for nom in poses if nom.startswith("workflow::")] == ["workflow::termine"]


def test_reconcile_un_ticket_illisible_n_est_pas_pris_pour_un_ticket_sans_etat(
    depot: Depot,
) -> None:
    """Lecture en échec = on ne touche à rien, et on le dit.

    Le piège est précis : `gl_issue_owner | cut` rendrait le code de `cut`, toujours 0, et le
    statut vide qui en sort se lit comme « aucun cycle de vie » — donc « à poser ». Un ticket
    illisible serait déclaré « Terminé ».
    """
    depot.pose_etat(iids_illisibles=["212"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode != 0
    assert depot.ecritures() == []


def test_reconcile_check_liste_sans_rien_ecrire(depot: Depot) -> None:
    """`--check` est un diagnostic : il nomme le passage à venir et n'écrit pas."""
    depot.pose_etat(labels_ticket=["type::infra", "workflow::en-revue"])
    acheve = depot.lib("reconcile-workflow", "--check", "212")
    assert acheve.returncode == 0, acheve.stderr
    assert "#212" in acheve.stdout
    assert "En revue" in acheve.stdout
    assert depot.ecritures() == []


def test_reconcile_balayage_ne_retient_que_les_fermes_restes_actifs(depot: Depot) -> None:
    """Sans argument : une seule lecture du backlog fermé, et les états finaux sont laissés."""
    depot.pose_etat(
        backlog_closed=[
            {"iid": "101", "labels": ["type::infra", "workflow::en-revue"]},
            {"iid": "102", "labels": ["type::feature", "workflow::termine"]},
            {"iid": "103", "labels": ["type::bug", "workflow::abandonne"]},
            {"iid": "104", "labels": ["type::doc", "workflow::en-cours"]},
            {"iid": "105", "labels": ["type::infra", "workflow::a-faire"]},
        ],
        labels_par_iid={
            "101": ["type::infra", "workflow::en-revue"],
            "104": ["type::doc", "workflow::en-cours"],
            "105": ["type::infra", "workflow::a-faire"],
        },
    )
    acheve = depot.lib("reconcile-workflow")
    assert acheve.returncode == 0, acheve.stderr
    ecritures = depot.ecritures()
    assert len(ecritures) == 3, f"attendu 101/104/105, reçu : {ecritures}"
    for argv in ecritures:
        poses = _labels_poses(argv)
        assert [nom for nom in poses if nom.startswith("workflow::")] == ["workflow::termine"]


def test_reconcile_balayage_sans_derive_ne_dit_rien_a_faire_et_n_ecrit_rien(depot: Depot) -> None:
    """Backlog fermé sain : le balayage le dit et s'arrête — aucune lecture par ticket."""
    depot.pose_etat(
        backlog_closed=[{"iid": "102", "labels": ["type::feature", "workflow::termine"]}]
    )
    acheve = depot.lib("reconcile-workflow")
    assert acheve.returncode == 0, acheve.stderr
    assert "rien à réconcilier" in acheve.stdout
    assert depot.ecritures() == []


def test_le_conftest_ne_laisse_plus_fuir_de_forge(depot: Depot) -> None:
    """`MAESTRO_FORGE` est retirée du dépôt (#344) : plus rien ne doit la lire ni la poser.

    Le garde-fou qu'il y avait ici épinglait `MAESTRO_FORGE=gitlab`, sans quoi ce module partait
    vers le mauvais backend et tombait sur des erreurs d'authentification dont rien ne désignait la
    cause. Le commutateur n'existe plus ; ce qui reste à garder est qu'il ne revienne pas par la
    bande — un bloc `env` de `.claude/settings.local.json` la pose encore sur les postes qui l'ont
    connue, et une variable ressuscitée en silence est précisément ce que le conftest neutralise
    (même famille que MAESTRO_ORCHESTRATE_COULEUR, #236).
    """
    lib = (RACINE / "scripts/gitlab/lib.sh").read_text(encoding="utf-8")
    assert "${MAESTRO_FORGE" not in lib, "lib.sh ne doit plus lire MAESTRO_FORGE (#344)"

    # Et le verdict ne dépend pas du poste : posée dans l'environnement, elle reste sans effet.
    environnement = dict(os.environ, MAESTRO_FORGE="gitlab")
    assert BASH is not None
    acheve = subprocess.run(  # noqa: S603
        [BASH, str(depot.racine / "scripts/gitlab/lib.sh"), "forge-cli"],
        cwd=str(depot.racine),
        env={
            **environnement,
            "PATH": os.pathsep.join([str(depot.fauxbin), environnement.get("PATH", "")]),
            "MAESTRO_FAUX_GH": str(depot.etat_json),
            "MAESTRO_FAUX_GH_ECRITURES": str(depot.ecritures_log),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert acheve.stdout.strip() == "gh"
