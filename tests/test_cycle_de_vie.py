"""Tests du cycle de vie porté par les labels `workflow::*` (ticket #212, chantier #207).

**L'invariant couvert ici est celui que GitLab n'assure plus.** Le cycle de vie était porté par le
champ Status natif, où « un seul statut à la fois » était une garantie du produit. Depuis #207 il
est porté par une famille de labels scopés, et sur le plan **Free** le `::` d'un label scopé n'est
que **cosmétique** : rien n'empêche un ticket de porter `workflow::a-faire` **et**
`workflow::en-revue`. L'exclusion mutuelle est donc à la charge de l'outillage —

    toute pose AJOUTE la cible et RETIRE les cinq autres dans la MÊME mutation

— et c'est la régression la plus probable du dispositif : un `addLabelIds` sans `removeLabelIds`
passerait tous les tests de bout en bout (le ticket *porte* bien son nouvel état), ne se verrait
pas à l'œil nu sur une ligne de backlog (les lectures rendent le premier label rencontré) et ne se
manifesterait que plus tard, en tickets à deux états et en colonnes de board dédoublées.

Deux helpers posent le cycle de vie, et les deux sont vérifiés ici : `set-workflow` (toutes les
commandes) et `begin` (le démarrage groupé de `/ticket-start`, où la pose voyage avec l'assignation
et les dates dans une mutation multi-widgets — un endroit facile à oublier).

Même parti pris que [`test_collaboration.py`](test_collaboration.py) : un **dépôt jetable** dans
`tmp_path`, le VRAI `lib.sh`, et un `glab` factice en tête du `PATH` qui journalise les mutations
reçues. **Ni réseau, ni compte GitLab, ni écriture** dans le dépôt de travail.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")

PROJET = "equipe-test/maestro"

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

#: Les GID que le faux GitLab attribue aux labels. Volontairement **non contigus et non ordonnés** :
#: rien dans `lib.sh` ne doit pouvoir deviner un GID à partir d'un autre (aucun ID en dur, cf. le
#: contrat) — un helper qui calculerait `base + rang` passerait sur des GID contigus.
GIDS = {
    "a-faire": "gid://gitlab/ProjectLabel/9007",
    "en-cours": "gid://gitlab/ProjectLabel/31",
    "en-revue": "gid://gitlab/ProjectLabel/4512",
    "termine": "gid://gitlab/ProjectLabel/88",
    "abandonne": "gid://gitlab/ProjectLabel/1203",
    "doublon": "gid://gitlab/ProjectLabel/677",
}

WORKITEM = "gid://gitlab/WorkItem/55501"

# --- Le glab factice -----------------------------------------------------------------------------
# Piloté par $MAESTRO_FAUX_GLAB (état JSON) et $MAESTRO_FAUX_GLAB_MUTATIONS (une requête par ligne).
# Il ne connaît que ce dont ce module a besoin : lire un work item, lister les labels du scope, et
# accuser réception d'une mutation.
FAUX_GLAB = r'''
import json
import os
import sys

with open(os.environ["MAESTRO_FAUX_GLAB"], encoding="utf-8") as f:
    etat = json.load(f)

args = sys.argv[1:]


def sortie(texte="", code=0):
    # Écriture en octets : sous Windows, sys.stdout encoderait en cp1252 et rendrait du mojibake
    # là où l'API GitLab renvoie de l'UTF-8 (« À faire », « Terminé »…). Piège de #141.
    sys.stdout.buffer.write(texte.encode("utf-8"))
    sys.stdout.buffer.flush()
    raise SystemExit(code)


def compact(obj):
    # L'outillage parse en grep/awk sur le JSON BRUT : pas d'espaces, pas d'échappement des
    # non-ASCII, sinon les motifs de lib.sh ne matchent plus.
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"


if args[:2] == ["auth", "status"]:
    sortie(code=0)

if args[:2] == ["api", "user"]:
    sortie(compact({"username": etat["moi"], "id": 4242}))

if args[:2] == ["api", "graphql"]:
    requete = "".join(a[len("query="):] for a in args[2:] if a.startswith("query="))

    if requete.lstrip().startswith("mutation"):
        with open(os.environ["MAESTRO_FAUX_GLAB_MUTATIONS"], "a", encoding="utf-8") as f:
            f.write(requete.replace("\n", " ") + "\n")
        if etat.get("mutation_en_echec"):
            sortie(compact({"data": {"workItemUpdate": {"errors": ["refus simulé"]}}}))
        sortie(compact({"data": {"workItemUpdate": {"errors": []}}}))

    # Liste des labels du scope — la brique qui permet de retirer les cinq autres.
    if "labels(searchTerm:" in requete:
        sortie(compact({"data": {"project": {"labels": {"nodes": etat["labels"]}}}}))

    # Backlog fermé : ce que balaie `reconcile-workflow` sans argument (#275). Chaque entrée de
    # `backlog_closed` est un « {iid, labels} » ; le reste de la forme (titre, assignés) est ce que
    # la vraie requête ramène et que le filtre traverse sans le lire.
    if "workItems(state:" in requete:
        noeuds = [
            {
                "iid": entree["iid"],
                "title": entree.get("titre", f"ticket {entree['iid']}"),
                "widgets": [
                    {"labels": {"nodes": [{"title": t} for t in entree.get("labels", [])]}},
                    {"assignees": {"nodes": []}},
                ],
            }
            for entree in etat.get("backlog_closed", [])
        ]
        sortie(compact({"data": {"project": {"workItems": {"nodes": noeuds}}}}))

    # Lecture d'un work item (gid seul, ou gid + dates + labels pour `begin`).
    if "workItems(iids:" in requete:
        iid = ""
        debut = requete.find('workItems(iids:["')
        if debut != -1:
            iid = requete[debut + len('workItems(iids:["'):].split('"', 1)[0]
        # Labels du ticket visé : `labels_par_iid` permet à un test d'en décrire plusieurs (le
        # balayage de #275 en traite N d'affilée) ; sinon tous partagent `labels_ticket`.
        titres = etat.get("labels_par_iid", {}).get(iid, etat.get("labels_ticket", []))
        noeud = {"id": etat["workitem"]}
        if "StartAndDueDate" in requete:
            noeud["widgets"] = [
                {"startDate": etat.get("start_date")},
                {"labels": {"nodes": [{"title": t} for t in titres]}},
            ]
        elif "WorkItemWidgetAssignees" in requete:
            # La requête de `gl_issue_owner`, sur laquelle `reconcile-workflow <iid>` s'appuie pour
            # ne jamais écraser un « Abandonné »/« Doublon ».
            if iid in etat.get("iids_illisibles", []):
                sortie(compact({"data": {"project": {"workItems": {"nodes": []}}}}))
            noeud = {
                "widgets": [
                    {"labels": {"nodes": [{"title": t} for t in titres]}},
                    {"assignees": {"nodes": []}},
                ]
            }
        sortie(compact({"data": {"project": {"workItems": {"nodes": [noeud]}}}}))

    sortie(code=1)

sortie(code=1)
'''


@dataclass
class Depot:
    """Dépôt jetable équipé du vrai `lib.sh` et d'un `glab` factice."""

    racine: Path
    fauxbin: Path
    etat_json: Path
    mutations_log: Path
    etat: dict

    def pose_etat(self, **entrees: object) -> None:
        self.etat.update(entrees)
        self.etat_json.write_text(
            json.dumps(self.etat, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )

    def mutations(self) -> list[str]:
        """Les mutations GraphQL reçues, une par ligne (vide si aucune)."""
        if not self.mutations_log.exists():
            return []
        lignes = self.mutations_log.read_text(encoding="utf-8").splitlines()
        return [ligne for ligne in lignes if ligne]

    def lib(self, *args: str) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        environnement.update(
            {
                "HOME": str(self.racine.parent / "home"),
                "PATH": os.pathsep.join([str(self.fauxbin), environnement.get("PATH", "")]),
                "GL_PROJECT": PROJET,
                # Le retry ne sert qu'aux hoquets réseau : une réponse volontairement muette ne
                # doit pas coûter trois secondes au test.
                "GL_GQL_RETRIES": "1",
                "GL_GQL_RETRY_DELAY": "0",
                "MAESTRO_FAUX_GLAB": str(self.etat_json),
                "MAESTRO_FAUX_GLAB_MUTATIONS": str(self.mutations_log),
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

    # Le glab factice : un script Python derrière un lanceur nommé `glab` (sans extension), pour que
    # le `command -v glab` de lib.sh le trouve comme le vrai.
    (fauxbin / "faux_glab.py").write_text(FAUX_GLAB, encoding="utf-8", newline="\n")
    lanceur = fauxbin / "glab"
    interpreteur = sys.executable.replace(chr(92), "/")
    lanceur.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{interpreteur}" "{(fauxbin / "faux_glab.py").as_posix()}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    lanceur.chmod(0o755)

    depot = Depot(
        racine=racine,
        fauxbin=fauxbin,
        etat_json=tmp_path / "faux-glab.json",
        mutations_log=tmp_path / "mutations.log",
        etat={},
    )
    depot.pose_etat(
        moi="MaestroAgents",
        workitem=WORKITEM,
        labels=[{"id": GIDS[s], "title": f"workflow::{s}"} for s in SLUGS],
        labels_ticket=["type::infra", "prio::moyenne", "workflow::a-faire"],
        start_date=None,
    )
    return depot


# --- Lecture d'une mutation ----------------------------------------------------------------------


def _liste(mutation: str, champ: str) -> list[str]:
    """Les GID passés à `addLabelIds`/`removeLabelIds` dans une mutation, dans l'ordre reçu."""
    trouve = re.search(rf"{champ}:\[(.*?)\]", mutation)
    if trouve is None:
        return []
    return re.findall(r'"([^"]+)"', trouve.group(1))


def _mutation_unique(depot: Depot) -> str:
    """La seule mutation attendue — le « dans le MÊME appel » de l'invariant est ici."""
    mutations = depot.mutations()
    assert len(mutations) == 1, f"une seule mutation attendue, reçu {len(mutations)} : {mutations}"
    return mutations[0]


# =================================================================================================
# L'invariant : poser un état retire les cinq autres
# =================================================================================================


@pytest.mark.parametrize(("libelle", "slug"), sorted(LIBELLES.items()))
def test_set_workflow_ajoute_la_cible_et_retire_les_cinq_autres(
    depot: Depot, libelle: str, slug: str
) -> None:
    """Pour CHACUN des six états : un `addLabelIds`, et les cinq autres en `removeLabelIds`.

    Paramétré sur les six plutôt que sur un cas représentatif : une table de correspondance se
    complète à la main (six `case` dans `gl_workflow_slug`), et c'est exactement le genre d'endroit
    où un état s'oublie — il faut que le test tombe sur celui-là, pas sur « un état ».
    """
    acheve = depot.lib("set-workflow", "212", libelle)
    assert acheve.returncode == 0, acheve.stderr

    mutation = _mutation_unique(depot)
    assert _liste(mutation, "addLabelIds") == [GIDS[slug]]
    assert sorted(_liste(mutation, "removeLabelIds")) == sorted(
        GIDS[autre] for autre in SLUGS if autre != slug
    )


def test_set_workflow_pose_le_cycle_de_vie_sur_le_bon_work_item(depot: Depot) -> None:
    """La cible de la mutation est le work item résolu depuis l'iid, pas l'iid lui-même."""
    assert depot.lib("set-workflow", "212", "En revue").returncode == 0
    assert f'id:"{WORKITEM}"' in _mutation_unique(depot)


def test_set_workflow_accepte_le_slug_comme_le_libelle(depot: Depot) -> None:
    """Contrat d'entrée : « en-cours » ≡ « En cours ». Les deux produisent la MÊME mutation."""
    assert depot.lib("set-workflow", "212", "En cours").returncode == 0
    par_libelle = _mutation_unique(depot)

    depot.mutations_log.unlink()
    assert depot.lib("set-workflow", "212", "en-cours").returncode == 0
    assert _mutation_unique(depot) == par_libelle


def test_set_workflow_rend_le_libelle_jamais_le_slug(depot: Depot) -> None:
    """Contrat de sortie : le slug est un détail de stockage, il ne sort pas de `lib.sh`."""
    acheve = depot.lib("set-workflow", "212", "termine")
    assert acheve.returncode == 0, acheve.stderr
    assert "Terminé" in acheve.stdout
    assert "termine" not in acheve.stdout


def test_set_workflow_ne_devine_aucun_gid(depot: Depot) -> None:
    """Les labels sont re-dérivés par NOM : des GID tout autres passent sans rien changer.

    C'est ce qui rend le dispositif robuste à une recréation des labels (un
    `bootstrap.sh` rejoué sur un projet neuf) — l'équivalent, côté labels, de l'absence de GID de
    statut en dur qu'on avait déjà du temps du lifecycle.
    """
    autres = {slug: f"gid://gitlab/GroupLabel/{700000 + i}" for i, slug in enumerate(SLUGS)}
    depot.pose_etat(labels=[{"id": autres[s], "title": f"workflow::{s}"} for s in SLUGS])

    assert depot.lib("set-workflow", "212", "En revue").returncode == 0
    mutation = _mutation_unique(depot)
    assert _liste(mutation, "addLabelIds") == [autres["en-revue"]]
    assert sorted(_liste(mutation, "removeLabelIds")) == sorted(
        autres[a] for a in SLUGS if a != "en-revue"
    )


def test_set_workflow_ne_touche_pas_aux_labels_de_categorisation(depot: Depot) -> None:
    """`type::`/`agent::`/`prio::` ne sont ni ajoutés ni retirés : seul le scope du cycle de vie.

    `labelsWidget` est additif, donc l'omission suffit à les préserver — encore faut-il qu'aucun
    d'eux ne se glisse dans `removeLabelIds`, ce qu'un « on retire tout et on repose » ferait.
    """
    assert depot.lib("set-workflow", "212", "En cours").returncode == 0
    mutation = _mutation_unique(depot)
    touches = _liste(mutation, "addLabelIds") + _liste(mutation, "removeLabelIds")
    assert set(touches) == set(GIDS.values())


# =================================================================================================
# `begin` — la même exclusion, dans la mutation groupée de /ticket-start
# =================================================================================================


def test_begin_retire_aussi_les_cinq_autres(depot: Depot) -> None:
    """Le démarrage groupé pose « En cours » : l'exclusion doit y être, pas seulement dans
    `set-workflow`.

    C'est le point d'oubli naturel du dispositif : la pose y est noyée dans une mutation
    multi-widgets (assignation + labels + dates) écrite à part de `gl_set_workflow`.
    """
    acheve = depot.lib("begin", "212")
    assert acheve.returncode == 0, acheve.stderr

    mutation = _mutation_unique(depot)
    assert _liste(mutation, "addLabelIds") == [GIDS["en-cours"]]
    assert sorted(_liste(mutation, "removeLabelIds")) == sorted(
        GIDS[autre] for autre in SLUGS if autre != "en-cours"
    )


def test_begin_groupe_tout_dans_une_seule_mutation(depot: Depot) -> None:
    """Assignation, cycle de vie et dates voyagent ensemble — un seul aller-retour réseau."""
    assert depot.lib("begin", "212").returncode == 0
    mutation = _mutation_unique(depot)
    for widget in ("assigneesWidget:", "labelsWidget:", "startAndDueDateWidget:"):
        assert widget in mutation, f"{widget} absent de la mutation groupée"


# =================================================================================================
# Refus : ce qui ne doit RIEN écrire
# =================================================================================================


def test_set_workflow_refuse_une_valeur_inconnue_sans_rien_ecrire(depot: Depot) -> None:
    """Une valeur hors vocabulaire s'arrête avant la mutation, en listant les six attendues.

    Sans ce refus, une faute de frappe (« en revu ») poserait un septième label hors scope — ou,
    pire, retirerait les six sans rien remettre.
    """
    acheve = depot.lib("set-workflow", "212", "en revu")
    assert acheve.returncode != 0
    assert "inconnue" in acheve.stderr
    for libelle in LIBELLES:
        assert libelle in acheve.stderr
    assert depot.mutations() == [], "aucune écriture ne doit partir sur une valeur inconnue"


def test_set_workflow_refuse_si_les_labels_ne_sont_pas_provisionnes(depot: Depot) -> None:
    """Projet sans `workflow::*` : on s'arrête en renvoyant vers `bootstrap.sh`.

    Poser la cible sans pouvoir retirer les autres serait précisément la pose partielle que tout
    ce module cherche à empêcher — mieux vaut ne rien écrire.
    """
    depot.pose_etat(labels=[])
    acheve = depot.lib("set-workflow", "212", "En cours")
    assert acheve.returncode != 0
    assert "bootstrap.sh" in acheve.stderr
    assert depot.mutations() == []


def test_set_workflow_signale_un_refus_de_gitlab(depot: Depot) -> None:
    """Une mutation qui revient avec des `errors` est un échec — pas un succès silencieux."""
    depot.pose_etat(mutation_en_echec=True)
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


def _cible_ajoutee(mutation: str) -> list[str]:
    return _liste(mutation, "addLabelIds")


def test_reconcile_pose_termine_sur_un_ticket_cible_reste_actif(depot: Depot) -> None:
    """Le cas nominal du ramassage : un ticket « En revue » soldé passe à « Terminé »."""
    depot.pose_etat(labels_ticket=["type::infra", "workflow::en-revue"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode == 0, acheve.stderr
    mutation = _mutation_unique(depot)
    assert _cible_ajoutee(mutation) == [GIDS["termine"]]
    # L'invariant de tout ce module vaut aussi ici : la pose passe par `set-workflow`, donc les cinq
    # autres partent dans le même appel. Une réconciliation qui écrirait son propre `addLabelIds`
    # laisserait le ticket à deux états.
    assert sorted(_liste(mutation, "removeLabelIds")) == sorted(
        GIDS[s] for s in SLUGS if s != "termine"
    )


@pytest.mark.parametrize("final", ["abandonne", "doublon"])
def test_reconcile_n_ecrase_jamais_un_etat_final(depot: Depot, final: str) -> None:
    """Un ticket « Abandonné »/« Doublon » est fermé lui aussi — et ne devient JAMAIS « Terminé ».

    C'est la seule règle de cette fonction qu'on ne peut pas rattraper après coup : le label
    d'origine est perdu par la pose, et rien dans le ticket ne dirait qu'il a été abandonné.
    """
    depot.pose_etat(labels_ticket=["type::infra", f"workflow::{final}"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode == 0, acheve.stderr
    assert depot.mutations() == [], f"« {final} » a été écrasé"


def test_reconcile_saute_un_ticket_deja_termine(depot: Depot) -> None:
    """Déjà « Terminé » : rien à écrire. C'est le cas nominal en régime établi, à chaque `gc`."""
    depot.pose_etat(labels_ticket=["type::infra", "workflow::termine"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode == 0, acheve.stderr
    assert depot.mutations() == []


def test_reconcile_pose_sur_un_ticket_sans_aucun_cycle_de_vie(depot: Depot) -> None:
    """Zéro label `workflow::` (ticket créé depuis l'UI GitLab) : soldé, donc « Terminé »."""
    depot.pose_etat(labels_ticket=["type::infra"])
    acheve = depot.lib("reconcile-workflow", "212")
    assert acheve.returncode == 0, acheve.stderr
    assert _cible_ajoutee(_mutation_unique(depot)) == [GIDS["termine"]]


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
    assert depot.mutations() == []


def test_reconcile_check_liste_sans_rien_ecrire(depot: Depot) -> None:
    """`--check` est un diagnostic : il nomme le passage à venir et n'écrit pas."""
    depot.pose_etat(labels_ticket=["type::infra", "workflow::en-revue"])
    acheve = depot.lib("reconcile-workflow", "--check", "212")
    assert acheve.returncode == 0, acheve.stderr
    assert "#212" in acheve.stdout
    assert "En revue" in acheve.stdout
    assert depot.mutations() == []


def test_reconcile_balayage_ne_retient_que_les_fermes_restes_actifs(depot: Depot) -> None:
    """Sans argument : une seule lecture du backlog fermé, et les états finaux sont laissés."""
    depot.pose_etat(
        backlog_closed=[
            {"iid": "101", "labels": ["type::infra", "workflow::en-revue"]},
            {"iid": "102", "labels": ["type::feature", "workflow::termine"]},
            {"iid": "103", "labels": ["type::bug", "workflow::abandonne"]},
            {"iid": "104", "labels": ["type::doc", "workflow::en-cours"]},
            {"iid": "105", "labels": ["type::infra", "workflow::a-faire"]},
        ]
    )
    acheve = depot.lib("reconcile-workflow")
    assert acheve.returncode == 0, acheve.stderr
    mutations = depot.mutations()
    assert len(mutations) == 3, f"attendu 101/104/105, reçu : {mutations}"
    for mutation in mutations:
        assert _cible_ajoutee(mutation) == [GIDS["termine"]]


def test_reconcile_balayage_sans_derive_ne_dit_rien_a_faire_et_n_ecrit_rien(depot: Depot) -> None:
    """Backlog fermé sain : le balayage le dit et s'arrête — aucune lecture par ticket."""
    depot.pose_etat(
        backlog_closed=[{"iid": "102", "labels": ["type::feature", "workflow::termine"]}]
    )
    acheve = depot.lib("reconcile-workflow")
    assert acheve.returncode == 0, acheve.stderr
    assert "rien à réconcilier" in acheve.stdout
    assert depot.mutations() == []
