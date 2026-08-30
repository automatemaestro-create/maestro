"""Le merge automatique et ses garde-fous — chantier #413, tests différés du lot #414.

Ce que ce chantier a renversé tient en une phrase : le merge n'attend plus un humain, **il n'attend
pas moins de vérifications**. Toute cette suite est là pour que la seconde moitié de la phrase reste
vraie — la première étant, elle, facile à vérifier et sans intérêt à garder.

Trois verbes de [`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh) et un hook :

* **`merge-mr`** (#415) — le SEUL chemin de merge du dépôt, et ses quatre prérequis. Chacun est
  éprouvé **isolément** : un test qui les vérifierait tous ensemble ne dirait pas lequel garde, et
  c'est précisément ce qu'on veut savoir le jour où l'un d'eux tombe.
* **`pipeline-wait`** (#416, étendu par #595) — une situation, un code ; le plafond n'est pas un
  rouge, et une **attente de naissance** n'est ni l'un ni l'autre.
* **`merge-order`** (#416) — l'ordre le moins conflictuel, sur le graphe mesuré de #299.
* **`guard.sh`** (#417) — le `deny` tient, et aucun prompt du dépôt ne prescrit `gh pr merge`.

…et, depuis #460, **ce que la clôture fait de chaque code** : les deux causes réparables (`4`
pipeline rouge, `5` conflit) enchaînent d'office sur `/mr-fix`, les quatre autres non. Cette
conduite-là ne vit que dans un prompt, et c'est justement pourquoi elle se garde : elle décide
entre « réparer », « repasser » et « laisser à un humain », et un glissement d'un code à l'autre ne
se verrait nulle part ailleurs.

**Ni réseau ni compte de forge** : le harnais partagé [`harnais_forge.py`](harnais_forge.py)
monte un dépôt jetable (avec un `origin` local, réel) et un `gh` factice en tête du `PATH`. Il est
partagé avec `test_collaboration.py` et `test_cycle_de_vie.py` pour la raison qu'il énonce : deux
doubles à tenir d'accord seraient le premier moyen de rendre une suite verte sur une forme de
réponse que l'autre a corrigée depuis.

⚠ **Les conflits, eux, ne sont pas simulés.** `merge-mr` et `merge-order` tranchent par
`git merge-tree --write-tree`, un merge 3-way réel : les branches de ces tests portent donc de
VRAIS commits qui se contredisent, poussés dans un vrai `origin`. Bouchonner ce verdict-là aurait
testé le bouchon — et c'est justement la source dont #303 a montré qu'aucune autre ne pouvait porter
la décision (docs/10 §8.3).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    GIT,
    Depot,
    ecritures,
    monte_depot,
    regle_merge,
    regle_pr,
    regle_pr_fermante,
    regle_prs_ouvertes,
    regle_run,
    regle_run_absent,
)

RACINE = Path(__file__).resolve().parent.parent

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


# =====================================================================================
# Le décor : une PR mergeable, dont chaque test casse UN prérequis
# =====================================================================================
# Le parti pris de toute la première partie. On monte une situation où le merge DOIT avoir lieu,
# puis chaque test en retire exactement une pièce et vérifie que le merge n'a pas lieu, avec LE code
# de cette pièce-là. C'est ce qui distingue « un garde-fou a parlé » de « LE BON garde-fou a
# parlé » : les cinq refus se ressemblent à l'écran, et trois partagent le même code de retour.

IID = 7
BRANCHE = f"chore/{IID}-merge-automatique"
PR = 42


def _branche(depot: Depot, nom: str, fichiers: dict[str, str], pousse: bool = True) -> str:
    """Une branche RÉELLE partant de `main`, poussée dans l'`origin` du dépôt jetable.

    Rend son sha. Le `checkout main` de la fin ramène le dépôt à son point de départ : sans lui, la
    branche suivante partirait de celle-ci et le graphe ne serait plus celui qu'on décrit.
    """
    depot.git("checkout", "--quiet", "-b", nom, "main")
    for chemin, contenu in fichiers.items():
        (depot.racine / chemin).write_text(contenu, encoding="utf-8", newline="\n")
    depot.git("add", "-A")
    depot.git("commit", "--quiet", "-m", f"feat: {nom}")
    sha = depot.git("rev-parse", "HEAD")
    if pousse:
        depot.git("push", "--quiet", "origin", nom)
    depot.git("checkout", "--quiet", "main")
    return sha


def _mergeable(depot: Depot, **surcharges: object) -> str:
    """Le décor nominal : une PR ouverte, non brouillon, qui ferme #7, poussée, sans conflit, verte.

    Rend le sha de la branche. Les surcharges nommées remplacent une règle du `gh` factice — c'est
    par elles que chaque test retire SA pièce, le reste du décor restant valide.
    """
    sha = _branche(depot, BRANCHE, {"livrable.txt": "le travail du ticket\n"})
    pr = surcharges.get("pr", regle_pr(BRANCHE, pr=PR, sha=sha, ferme=(IID,)))
    run = surcharges.get("run", regle_run(BRANCHE, sha=sha))
    depot.pose_etat(
        graphql=[pr, regle_prs_ouvertes((BRANCHE,))],
        rest=[run],
        ecritures=[regle_merge(PR)],
    )
    return sha


def test_tout_vert_la_pr_est_mergee_en_squash(depot: Depot) -> None:
    """Le cas nominal, sans lequel aucun des refus qui suivent ne prouverait quoi que ce soit.

    Un décor où le merge n'aurait de toute façon pas lieu rendrait les cinq tests suivants verts
    pour la mauvaise raison : ils vérifieraient qu'un merge impossible n'a pas lieu.
    """
    sha = _mergeable(depot)
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 0, r.stdout + r.stderr
    put = [ligne for ligne in depot.appels() if f"pulls/{PR}/merge" in ligne]
    assert len(put) == 1, f"un merge, et un seul : {put}"
    assert "merge_method=squash" in put[0], "le dépôt merge en squash (docs/10 §6)"
    assert f"sha={sha}" in put[0], (
        "le sha voyage avec le PUT : sans lui, une tête qui bouge entre le contrôle et le merge "
        "passerait en silence — c'est-à-dire un merge non vérifié"
    )


# --- Prérequis 1 : une PR ouverte, non brouillon, qui ferme le ticket ---------------------------
# Quatre façons de ne pas l'avoir, un seul code (`6` — geste humain), et c'est voulu : aucune des
# quatre ne se répare en réessayant, et aucune ne se répare par `/mr-fix`. Ce qui doit les
# distinguer est le MESSAGE, que chaque test lit.
#
# La CINQUIÈME façon, elle, a son propre code depuis #593 : une PR DÉJÀ MERGÉE n'appelle aucun
# geste — l'état visé est atteint, par un autre chemin. Les tests qui la couvrent sont juste après
# ceux du `6`, et ils vérifient d'abord qu'elle n'est plus rendue comme les quatre autres : c'est
# la moitié qui garde, l'autre ne faisant que décrire le nouveau code.


def test_sans_pr_il_n_y_a_rien_a_merger(depot: Depot) -> None:
    _mergeable(depot, pr=regle_pr(BRANCHE, etat=""))
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 6, r.stdout + r.stderr
    assert "aucune PR" in r.stderr


def test_une_pr_fermee_n_est_pas_mergee(depot: Depot) -> None:
    sha = _branche(depot, BRANCHE, {"livrable.txt": "le travail du ticket\n"})
    depot.pose_etat(
        graphql=[regle_pr(BRANCHE, pr=PR, sha=sha, etat="CLOSED", ferme=(IID,))],
        rest=[regle_run(BRANCHE, sha=sha)],
        ecritures=[regle_merge(PR)],
    )
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 6, r.stdout + r.stderr
    assert "closed" in r.stderr, (
        "l'état est nommé — « fermée » et « déjà mergée » n'ont pas le même remède"
    )


def test_un_brouillon_n_est_pas_merge_et_n_est_pas_leve_au_passage(depot: Depot) -> None:
    """GitHub refuse de merger un brouillon ; `merge-mr` le NOMME au lieu de le lever.

    Lever le Draft ici ferait changer, à un verbe de merge, un état qu'il est censé constater :
    « Draft » dit « pas fini », et c'est la commande qui clôt le ticket qui le lève (#418).
    """
    _mergeable(depot, pr=regle_pr(BRANCHE, pr=PR, sha="", brouillon=True, ferme=(IID,)))
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 6, r.stdout + r.stderr
    assert "brouillon" in r.stderr
    assert "gh pr ready" in r.stderr, "le remède est nommé, il n'est pas joué"
    assert not [ligne for ligne in depot.appels() if "pr\tready" in ligne], (
        "constater n'est pas réparer : le verbe ne lève pas le brouillon lui-même"
    )


def test_une_pr_deja_mergee_rend_sept_et_non_six(depot: Depot) -> None:
    """La seule des cinq qui soit un SUCCÈS : la PR est dans `main`, personne n'a rien à faire.

    Le `6` disait « geste humain » — et le pilote, qui décide sur ce code, en tirait « bloquée ».
    Sur le run `20260826-183242` cela donnait « PR #590 (#582) non mergée » à propos d'une PR
    mergée, un ticket LIVRÉ compté parmi les bloqués, et le worktree jamais ramassé (#593).

    Le test vérifie les deux moitiés : le code n'est plus `6` (ce qui garde), et il n'est pas `0`
    non plus (le verbe n'a pas mergé — s'attribuer ce merge raconterait un run faux). Et surtout,
    AUCUN PUT : constater qu'une PR est mergée ne consiste pas à la merger une seconde fois.
    """
    sha = _branche(depot, BRANCHE, {"livrable.txt": "le travail du ticket\n"})
    depot.pose_etat(
        graphql=[regle_pr(BRANCHE, pr=PR, sha=sha, etat="MERGED", ferme=(IID,))],
        rest=[regle_run(BRANCHE, sha=sha)],
        ecritures=[regle_merge(PR)],
    )
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 7, r.stdout + r.stderr
    assert "déjà mergée" in r.stdout, (
        "sur stdout et sous « ✓ » : c'est un verdict positif, pas un refus"
    )
    assert ecritures(depot) == [], "constater n'est pas merger"


def test_un_iid_dont_la_pr_est_mergee_rend_sept_lui_aussi(depot: Depot) -> None:
    """L'autre chemin d'entrée, celui de `/ticket-finish` — sans lui le correctif serait à moitié.

    `gl_branche_du_ticket` ne regarde que les PR OUVERTES : une PR mergée y est indiscernable d'une
    PR jamais créée, et les deux sortaient en `6` sur le même message. On demande donc à la forge
    quelle PR ferme le ticket, PR mergées comprises, puis le flux normal juge la branche trouvée —
    une seule voie de sortie pour le `7`, quel que soit le chemin d'entrée.
    """
    sha = _branche(depot, BRANCHE, {"livrable.txt": "le travail du ticket\n"})
    depot.pose_etat(
        graphql=[
            regle_pr(BRANCHE, pr=PR, sha=sha, etat="MERGED", ferme=(IID,)),
            regle_prs_ouvertes(()),
            regle_pr_fermante((("MERGED", BRANCHE),)),
        ],
        rest=[regle_run(BRANCHE, sha=sha)],
        ecritures=[regle_merge(PR)],
    )
    r = depot.lib("merge-mr", str(IID))
    assert r.returncode == 7, r.stdout + r.stderr
    assert ecritures(depot) == []


def test_un_iid_sans_aucune_pr_reste_un_six(depot: Depot) -> None:
    """L'autre moitié de la question posée à la forge : aucune PR ne ferme le ticket.

    Sans ce test, rendre `7` dès qu'il n'y a pas de PR ouverte passerait — et le verbe déclarerait
    « déjà mergé » un ticket qui n'a jamais eu de PR.
    """
    _branche(depot, BRANCHE, {"livrable.txt": "le travail du ticket\n"})
    depot.pose_etat(graphql=[regle_prs_ouvertes(()), regle_pr_fermante()])
    r = depot.lib("merge-mr", str(IID))
    assert r.returncode == 6, r.stdout + r.stderr
    assert "aucune PR" in r.stderr


def test_une_pr_qui_ne_ferme_pas_son_ticket_n_est_pas_mergee(depot: Depot) -> None:
    """Sans `Closes`, le merge laisserait le ticket ouvert ET sans état.

    Personne ne le reposerait : le workflow `issues: closed` (#377) n'aurait aucun événement à
    écouter. C'est le prérequis le moins intuitif des quatre, et celui dont l'oubli est silencieux.
    """
    _mergeable(depot, pr=regle_pr(BRANCHE, pr=PR, sha="", ferme=(999,)))
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 6, r.stdout + r.stderr
    assert f"ne ferme pas #{IID}" in r.stderr
    assert "set-mr-description" in r.stderr, "le remède passe par le helper, pas par un --body"


# --- Prérequis 2 : rien de non poussé -----------------------------------------------------------


def test_des_commits_non_pousses_empechent_le_merge(depot: Depot) -> None:
    """Merger moins que ce qui existe est une perte SILENCIEUSE — la seule que rien ne rattrape.

    La PR est verte et sans conflit : le seul défaut est un commit local que la forge n'a pas.
    """
    sha = _mergeable(depot)
    depot.git("checkout", "--quiet", BRANCHE)
    (depot.racine / "oublie.txt").write_text("jamais poussé\n", encoding="utf-8", newline="\n")
    depot.git("add", "-A")
    depot.git("commit", "--quiet", "-m", "feat: le commit que la PR n'a pas")
    local = depot.git("rev-parse", "HEAD")
    depot.git("checkout", "--quiet", "main")

    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 6, r.stdout + r.stderr
    assert "des commits que la PR n'a pas" in r.stderr
    assert local[:8] in r.stderr and sha[:8] in r.stderr, "les deux têtes sont nommées"
    assert not [ligne for ligne in depot.appels() if "/merge" in ligne]


def test_un_local_en_retard_ne_bloque_pas_le_merge(depot: Depot) -> None:
    """Le local EN RETARD n'est pas notre affaire : quelqu'un a poussé depuis, la PR fait foi.

    Sans cette asymétrie, le pilote — qui ne sort jamais les branches qu'il merge — refuserait de
    merger tout ce qu'une session vient de pousser depuis son worktree.
    """
    _branche(depot, BRANCHE, {"livrable.txt": "le travail du ticket\n"})
    depot.git("checkout", "--quiet", BRANCHE)
    (depot.racine / "suite.txt").write_text("poussé ensuite\n", encoding="utf-8", newline="\n")
    depot.git("add", "-A")
    depot.git("commit", "--quiet", "-m", "feat: la suite")
    depot.git("push", "--quiet", "origin", BRANCHE)
    tete = depot.git("rev-parse", "HEAD")
    depot.git("checkout", "--quiet", BRANCHE + "~1", "--")
    depot.git("reset", "--quiet", "--hard", "HEAD~1")
    depot.git("checkout", "--quiet", "main")

    depot.pose_etat(
        graphql=[regle_pr(BRANCHE, pr=PR, sha=tete, ferme=(IID,))],
        rest=[regle_run(BRANCHE, sha=tete)],
        ecritures=[regle_merge(PR)],
    )
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 0, r.stdout + r.stderr


# --- Prérequis 3 : aucun conflit réel avec origin/main -------------------------------------------


def test_un_conflit_avec_origin_main_empeche_le_merge(depot: Depot) -> None:
    """`5` — réparable, mais pas ici : c'est `/mr-fix` qui résout, par un merge et jamais un rebase.

    Le conflit est RÉEL (deux commits qui se contredisent sur la même ligne), pas bouchonné : c'est
    tout le sujet de #303, où ni `behind-main` ni le champ de la forge ne pouvaient trancher.
    """
    sha = _branche(depot, BRANCHE, {"dispute.txt": "la version du ticket\n"})
    depot.git("checkout", "--quiet", "main")
    (depot.racine / "dispute.txt").write_text(
        "la version de main\n", encoding="utf-8", newline="\n")
    depot.git("add", "-A")
    depot.git("commit", "--quiet", "-m", "feat: main avance de son côté")
    depot.git("push", "--quiet", "origin", "main")

    depot.pose_etat(
        graphql=[regle_pr(BRANCHE, pr=PR, sha=sha, ferme=(IID,))],
        rest=[regle_run(BRANCHE, sha=sha)],
        ecritures=[regle_merge(PR)],
    )
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "conflit" in r.stderr
    assert "dispute.txt" in r.stderr, "le fichier en conflit est nommé, pas seulement compté"
    assert not [ligne for ligne in depot.appels() if "/merge" in ligne]


# --- Prérequis 4 : un pipeline vert, SUR LA TÊTE DE LA PR ----------------------------------------
# Les deux contrôles les plus importants de la suite. Ce sont eux que la protection de branche
# aurait tenus si elle existait sur ce plan (§8.8), et personne d'autre ne les tient.


def test_une_pr_au_pipeline_rouge_n_est_jamais_mergee(depot: Depot) -> None:
    _mergeable(depot, run=regle_run(BRANCHE, sha="", conclusion="failure"))
    sha = depot.git("rev-parse", BRANCHE)
    depot.pose_etat(rest=[regle_run(BRANCHE, sha=sha, conclusion="failure")])
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "failed" in r.stderr
    assert not [ligne for ligne in depot.appels() if "/merge" in ligne], (
        "AUCUN merge au rouge — c'est le faux verdict que tout le chantier existe pour empêcher"
    )


def test_un_vert_porte_par_un_sha_anterieur_n_est_pas_un_vert(depot: Depot) -> None:
    """`3`, pas `0` : un vert sur un commit antérieur ne dit RIEN du commit qu'on merge.

    C'est le cas nominal juste après un push — le run précédent est terminé, le nouveau n'a pas
    démarré —, donc le plus fréquent des deux et le seul qui ait l'air d'un feu vert.
    """
    _mergeable(depot, run=regle_run(BRANCHE, sha="0" * 40))
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "périmé" in r.stderr
    assert not [ligne for ligne in depot.appels() if "/merge" in ligne]


def test_sans_pipeline_on_repasse_plus_tard(depot: Depot) -> None:
    """`3` et non `4` : « pas encore de verdict » n'est pas « verdict défavorable ».

    Le run naît APRÈS la PR (la CI ne se déclenche que sur les PR, §8) : confondre les deux ferait
    ouvrir une session `/mr-fix` sur une PR qui n'a encore rien à réparer.
    """
    _mergeable(depot, run=regle_run_absent(BRANCHE))
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 3, r.stdout + r.stderr
    assert not [ligne for ligne in depot.appels() if "/merge" in ligne]


def test_un_pipeline_en_cours_laisse_la_pr_en_file(depot: Depot) -> None:
    _mergeable(depot, run=regle_run(BRANCHE, sha="", statut="in_progress", conclusion=""))
    sha = depot.git("rev-parse", BRANCHE)
    depot.pose_etat(rest=[regle_run(BRANCHE, sha=sha, statut="in_progress", conclusion="")])
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "running" in r.stderr


# --- Ce que `--check` promet ----------------------------------------------------------------------


def test_check_ne_merge_rien_et_n_ecrit_rien(depot: Depot) -> None:
    """`--check` rend le MÊME verdict sans le poser : c'est ce qui le rend appelable partout.

    Sans cette promesse, un `--check` posé dans un bilan de santé mergerait le dépôt en le
    diagnostiquant.
    """
    _mergeable(depot)
    r = depot.lib("merge-mr", BRANCHE, "--check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "mergeable" in r.stdout
    assert ecritures(depot) == [], f"aucune écriture côté forge : {ecritures(depot)}"


def test_un_refus_de_github_n_est_pas_un_merge(depot: Depot) -> None:
    """Le verbe lit `"merged":true` dans le corps, pas le code de sortie de `gh`.

    GitHub rend un 200 poli sur des refus (« Head branch was modified ») : conclure au merge sur le
    seul code de sortie annoncerait mergée une PR qui ne l'est pas — et le ticket serait déclaré
    livré alors que son travail est encore dehors.
    """
    _mergeable(depot)
    depot.pose_etat(ecritures=[regle_merge(PR, merge=False)])
    r = depot.lib("merge-mr", BRANCHE)
    assert r.returncode == 6, r.stdout + r.stderr
    assert "refusé par GitHub" in r.stderr


def test_main_n_est_pas_une_branche_de_ticket(depot: Depot) -> None:
    r = depot.lib("merge-mr", "main")
    assert r.returncode == 2, r.stdout + r.stderr
    assert ecritures(depot) == []


def test_un_iid_se_resout_en_branche_par_les_pr_ouvertes(depot: Depot) -> None:
    """La cible peut être un iid : c'est la forme qu'emploient `/ticket-finish` et le pilote.

    La branche vient des PR OUVERTES et non d'un `branch-for` recalculé : ce qu'on merge est une PR,
    donc sa branche de tête fait autorité — un slug qui aurait dérivé depuis la création de la
    branche rendrait le calcul faux là où cette lecture reste juste.
    """
    _mergeable(depot)
    r = depot.lib("merge-mr", str(IID))
    assert r.returncode == 0, r.stdout + r.stderr


# =====================================================================================
# `pipeline-wait` — une situation, un code (#416, étendu par #595)
# =====================================================================================
# Le plafond N'EST PAS un rouge, et c'est tout l'enjeu de la table : `4` dit « pas encore »,
# `3` dit « verdict rendu, défavorable », `5` dit « il n'y en aura pas », `6` dit « il n'est pas là,
# mais il vient » (#595, plus bas). Les confondre enverrait ouvrir une remédiation sur une PR qui
# n'a rien à réparer — ou, dans l'autre sens, attendre indéfiniment un verdict déjà tombé.

VITE = {"MAESTRO_PIPELINE_SONDAGE": "1", "MAESTRO_PIPELINE_NAISSANCE": "1"}


def test_un_pipeline_vert_rend_zero(depot: Depot) -> None:
    depot.pose_etat(rest=[regle_run("chore/1-x", sha="abc")])
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "5", reglages=VITE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == "success"


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "action_required"])
def test_un_verdict_terminal_non_vert_rend_trois(depot: Depot, conclusion: str) -> None:
    """Quatre conclusions, un seul code — et c'est délibéré.

    L'appelant qui doit distinguer `failed` de `skipped` lit le statut IMPRIMÉ ; celui qui décide
    « ne pas merger » n'a pas à énumérer quatre mots pour une seule conduite.
    """
    depot.pose_etat(rest=[regle_run("chore/1-x", sha="abc", conclusion=conclusion)])
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "5", reglages=VITE)
    assert r.returncode == 3, r.stdout + r.stderr
    assert r.stdout.strip() in ("failed", "canceled", "skipped", "manual")


def test_le_plafond_n_est_pas_un_rouge(depot: Depot) -> None:
    """`4` — le run tourne toujours. Le rendre en `3` déclencherait une remédiation sur un vert
    en devenir, et le rendre en `0` mergerait sur un verdict jamais rendu."""
    depot.pose_etat(rest=[regle_run("chore/1-x", sha="abc", statut="in_progress", conclusion="")])
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "1", reglages=VITE)
    assert r.returncode == 4, r.stdout + r.stderr
    assert r.stdout.strip() == "running", "le dernier statut connu, pas un « failed » inventé"
    assert "plafond atteint" in r.stderr


def test_aucun_pipeline_rend_cinq(depot: Depot) -> None:
    """`5` — « il n'y en aura pas », borné par le délai de NAISSANCE et non par le plafond.

    Attendre quinze minutes pour conclure à une absence acquise en deux serait payer une ignorance
    au prix d'une autre : un run qui n'est pas né n'a plus d'événement pour le déclencher.
    """
    depot.pose_etat(rest=[])
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "600", reglages=VITE)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "aucun pipeline" in r.stderr


def test_pipeline_wait_n_ecrit_rien(depot: Depot) -> None:
    """Un verbe d'ATTENTE, et rien d'autre : il ne relance rien, ne corrige rien, ne juge rien.

    Le vérifier sur le cas ROUGE et pas sur le vert : c'est là qu'un verbe serviable serait tenté
    de rejouer le job, et c'est ce que `/mr-fix` seul a le droit de faire.
    """
    depot.pose_etat(rest=[regle_run("chore/1-x", sha="abc", conclusion="failure")])
    depot.lib("pipeline-wait", "chore/1-x", "--timeout", "5", reglages=VITE)
    assert ecritures(depot) == [], f"aucune écriture : {ecritures(depot)}"
    assert not [ligne for ligne in depot.appels() if "rerun" in ligne]


def test_timeout_zero_sonde_une_fois_sans_attendre(depot: Depot) -> None:
    """Relire l'état d'une branche avec la MÊME table de codes, sans la dupliquer ailleurs."""
    depot.pose_etat(rest=[regle_run("chore/1-x", sha="abc", statut="queued", conclusion="")])
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "0", reglages=VITE)
    assert r.returncode == 4, r.stdout + r.stderr


def test_un_run_id_illisible_n_est_pas_une_attente(depot: Depot) -> None:
    """`1` et non `5` : un id vient d'un appelant qui l'avait en main, donc c'est une erreur de
    lecture — pas un run « pas encore né », qu'aucune attente ne fera apparaître."""
    depot.pose_etat(rest=[])
    r = depot.lib("pipeline-wait", "12345", "--timeout", "0", reglages=VITE)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "illisible" in r.stderr


# =====================================================================================
# L'attente de NAISSANCE — quand l'événement arrive en retard (#595)
# =====================================================================================
# La mesure qui a ouvert le ticket est dans docs/10 §8.9 : le 2026-08-26, `pull_request` a mis 18 à
# 20 min à déclencher la CI sur trois PR consécutives. Sous les 120 s du délai de naissance d'alors,
# `pipeline-wait` déclarait donc anormal le cas normal du jour.
#
# Ce que ces tests gardent n'est PAS un chronomètre — un test qui mesurerait des minutes mesurerait
# la charge de la machine. Ce sont les trois décisions : deux délais qui ne partagent pas un
# chiffre, un run identifié par le sha qu'on attend, et un code qui ne route vers rien.
#
# ⚠ Le principe valait pour les DURÉES et pas encore pour les TRANSITIONS, et c'est ce que #648
# corrige : faire apparaître un run « au bout de deux secondes » est une horloge murale de plus,
# simplement déguisée en décor. Une transition s'obtient donc par le RANG DE L'APPEL — le double
# répond « absent » aux N premiers sondages et « présent » ensuite (`regle_run(..., apres=N)`) —,
# jamais par un minuteur qui court contre le démarrage d'un sous-processus.

# Deux délais volontairement DIFFÉRENTS et tous deux courts : ce qu'on observe est lequel des deux
# s'applique, jamais leur valeur de production.
NAISSANCE = {"MAESTRO_PIPELINE_SONDAGE": "1",
             "MAESTRO_PIPELINE_NAISSANCE": "1",
             "MAESTRO_PIPELINE_NAISSANCE_PR": "4"}
SHA_TETE = "a" * 40
SHA_AVANT = "0" * 40


def sondages(depot: Depot, branche: str = "chore/1-x") -> list[str]:
    """Les lectures du dernier run de la branche — exactement un appel par sondage du verbe.

    C'est par ce COMPTE que le rang d'apparition se vérifie (#648). Un chronomètre dirait la charge
    de la machine ; le nombre de sondages, lui, est ce que la boucle a réellement fait, et c'est
    déjà la règle du dépôt partout où l'on veut garder un gain sans le mesurer en secondes (#602).
    """
    return [ligne for ligne in depot.appels() if f"actions/runs?branch={branche}" in ligne]


def test_un_run_pas_encore_ne_sous_une_pr_ouverte_rend_six_et_non_cinq(depot: Depot) -> None:
    """`6` et non `5` : « il n'est pas là » et « il n'y en aura pas » n'appellent pas la même suite.

    Le chiffre compte au-delà de la taxonomie. `pipeline-wait` et `merge-mr` ont deux tables qui
    partagent leurs codes, et le `5` de `merge-mr` est un **conflit** — une cause réparable qui
    enchaîne d'office sur `/mr-fix` (#460). Un `5` de naissance lu dans la mauvaise table envoyait
    donc une remédiation sur une PR qui n'a ni conflit ni job rouge, et lui faisait consommer ses
    deux tentatives pour rien. Le `6`, lui, désigne des deux côtés un geste humain que rien ne
    tente.
    """
    depot.pose_etat(
        graphql=[regle_pr("chore/1-x", pr=PR, sha=SHA_TETE, ferme=(1,))],
        rest=[regle_run_absent("chore/1-x")],
    )
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "600", reglages=NAISSANCE)
    assert r.returncode == 6, r.stdout + r.stderr
    assert "pas encore né" in r.stderr
    assert f"#{PR}" in r.stderr, "la PR qui rend l'événement dû est nommée"
    # Le remède du 2026-08-26 était su de `ci.yml` et de /mr-fix, et introuvable au moment où il
    # servait : il est imprimé ICI, branche déjà substituée. Ce qui manquait n'était pas le geste.
    assert "gh workflow run ci.yml --ref chore/1-x" in r.stderr


def test_sans_pr_ouverte_le_delai_court_garde_son_motif(depot: Depot) -> None:
    """Le contre-échantillon, sans lequel le test précédent ne dirait pas D'OÙ vient le `6`.

    Le raisonnement de #416 — « un run qui n'est pas né deux minutes après le push ne naîtra pas » —
    reste JUSTE quand aucun événement n'est dû : il n'y a pas d'émetteur. Allonger l'attente dans ce
    cas-là serait payer quinze minutes une réponse acquise en deux, c'est-à-dire réintroduire le
    défaut symétrique de celui que #595 corrige.
    """
    depot.pose_etat(graphql=[regle_pr("chore/1-x", etat="")], rest=[regle_run_absent("chore/1-x")])
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "600", reglages=NAISSANCE)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "aucun pipeline" in r.stderr


def test_le_delai_de_naissance_sous_pr_ne_se_range_pas_sous_le_plafond(depot: Depot) -> None:
    """Les deux bornes ne mesurent pas la même chose, donc elles ne partagent pas un chiffre.

    C'est le défaut nommé par le ticket : un plafond censé borner une attente ANORMALE était devenu
    le mécanisme qui déclarait anormale une attente de naissance normale. Le plafond vaut ici 1 s et
    la naissance sous PR 4 s — si le second était ramené au premier, l'appel sortirait en `4` (« le
    run tourne toujours »), verdict qui n'a aucun sens sur un run qui n'existe pas.
    """
    depot.pose_etat(
        graphql=[regle_pr("chore/1-x", pr=PR, sha=SHA_TETE, ferme=(1,))],
        rest=[regle_run_absent("chore/1-x")],
    )
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "1", reglages=NAISSANCE)
    assert r.returncode == 6, (
        "le délai de naissance sous PR a été ramené au plafond — c'est le bug de #595, à l'envers"
        f"\n{r.stdout}{r.stderr}"
    )


def test_un_vieux_vert_n_est_pas_le_run_qu_on_attend(depot: Depot) -> None:
    """La moitié de #595 que le chronomètre ne montre pas.

    `pipeline-wait` tenait pour « vu » n'importe quel run de la branche, celui de la push
    précédente compris : sur une branche portant un vieux vert, il rendait `0` INSTANTANÉMENT. La
    reprise unique que `/ticket-finish` s'accorde sur un `3` (docs/10 §6) repassait alors sans avoir
    attendu une seule seconde, et les deux appels rendaient le même verdict pour la même raison —
    une reprise qui a l'air d'en être une sans jamais laisser rien arriver.

    L'A/B est le test : même vieux vert, seule la PR change. Ouverte, elle dit quel sha on attend et
    le vert est écarté ; absente, on retombe sur le régime d'avant #595 et il est pris tel quel.
    """
    vieux_vert = regle_run("chore/1-x", sha=SHA_AVANT)

    depot.pose_etat(graphql=[regle_pr("chore/1-x", etat="")], rest=[vieux_vert])
    temoin = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "600", reglages=NAISSANCE)
    assert temoin.returncode == 0, (
        "sans PR, aucun sha n'est attendu : le dernier run de la branche fait foi, comme avant"
        f"\n{temoin.stdout}{temoin.stderr}"
    )

    depot.pose_etat(graphql=[regle_pr("chore/1-x", pr=PR, sha=SHA_TETE, ferme=(1,))])
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "600", reglages=NAISSANCE)
    assert r.returncode == 6, (
        "un vert porté par le sha précédent a été pris pour le run attendu — le `0` instantané est "
        f"de retour\n{r.stdout}{r.stderr}"
    )


def test_une_naissance_qui_aboutit_le_dit_quand_meme(depot: Depot) -> None:
    """Le troisième critère du ticket, côté clôture : une attente qui DURE se nomme, même réussie.

    Sans cette ligne, une clôture qui aboutit après vingt minutes est indiscernable d'une clôture
    qui a mergé tout de suite — et le compte rendu ne dirait jamais que le déclencheur a tardé. Le
    seuil est le délai COURT : en deçà, la naissance est celle du régime normal (le run naît après
    la PR, c'est la règle et non une anomalie) et l'annoncer apprendrait à ne plus lire l'annonce.

    Le run apparaît en cours de route, comme dans la vraie vie : c'est la seule façon d'observer la
    TRANSITION, qu'un état figé ne peut pas produire.

    ⚠ ET ELLE S'OBSERVE AU RANG DU SONDAGE, JAMAIS À L'HORLOGE (#648). Le run apparaissait ici au
    bout de deux secondes, par un `threading.Timer` posé côté test ; ce que cette course mesurait
    en pratique était le **temps de démarrage du sous-processus** — sous seize workers, `bash` + le
    chargement de `lib.sh` + le premier appel au `gh` factice dépassent les deux secondes, si bien
    que le PREMIER sondage voyait déjà le run né. Le verbe rendait alors `0` et `success` — donc
    tout allait bien — mais sans la ligne, et l'assertion tombait. Un **faux rouge**, c'est-à-dire
    le pire des rouges : il n'apprend rien sur le code et il apprend à pousser quand même.
    Le double fait donc apparaître le run au TROISIÈME sondage (`apres=2`), et l'attente que le
    verbe nomme est celle qu'il compte lui-même (`attendu`, incrémenté du pas de sondage), pas
    celle qu'une machine chargée lui a fait subir : aucune assertion ne dépend plus d'une durée.

    L'A/B est le test, et il **prouve son motif avant de conclure** : même verbe, même décor, seul
    le rang d'apparition change. Né au premier sondage, le run ne doit RIEN faire dire — sans cette
    moitié, une sonde débranchée rendrait un ✓ sur une question jamais posée (#534/#537).
    """
    # Le témoin : la naissance du régime NORMAL (le run est déjà là au premier sondage), qui ne
    # s'annonce pas — l'annoncer apprendrait à ne plus lire l'annonce.
    depot.pose_etat(
        graphql=[regle_pr("chore/1-x", pr=PR, sha=SHA_TETE, ferme=(1,))],
        rest=[regle_run("chore/1-x", sha=SHA_TETE)],
    )
    temoin = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "600", reglages=NAISSANCE)
    assert temoin.returncode == 0, temoin.stdout + temoin.stderr
    assert "run né après" not in temoin.stderr, (
        "un run né tout de suite ne doit RIEN faire dire : c'est ce qui prouve que la ligne du cas "
        f"suivant vient bien de la naissance tardive\n{temoin.stderr}"
    )
    avant = len(sondages(depot))
    assert avant == 1, f"le témoin voit le run du premier coup : {sondages(depot)}"

    # Le cas : deux sondages « absent », le run n'apparaît qu'au troisième. L'attente comptée par le
    # verbe vaut alors 2 s — au-delà du délai court (1 s), en deçà de la naissance sous PR (4 s).
    depot.pose_etat(
        rest=[regle_run("chore/1-x", sha=SHA_TETE, apres=2), regle_run_absent("chore/1-x")],
    )
    r = depot.lib("pipeline-wait", "chore/1-x", "--timeout", "600", reglages=NAISSANCE)
    assert r.returncode == 0, f"le run est né, l'attente doit aboutir\n{r.stdout}{r.stderr}"
    assert r.stdout.strip() == "success"
    assert "run né après" in r.stderr, (
        "une naissance plus longue que le délai court doit être NOMMÉE — c'est ce que le résumé de "
        f"clôture relaie\n{r.stderr}"
    )
    assert len(sondages(depot)) - avant == 3, (
        "la transition doit tomber au TROISIÈME sondage, et c'est ce COMPTE qui l'atteste : plus "
        "tôt, l'attente du verbe ne dépasse pas le délai court et la ligne n'a pas lieu d'être ; "
        f"plus tard, `apres` ne compte pas ce qu'il annonce\n{sondages(depot)[avant:]}"
    )


def test_le_verbe_ne_declenche_jamais_le_pipeline_lui_meme(depot: Depot) -> None:
    """L'arbitrage de #595 : le dispatch de secours reste un geste HUMAIN.

    Deux raisons, dont une seule suffirait — `pipeline-wait` ne relance rien par construction (c'est
    ce qui lui vaut d'être autorisé en session de run là où `merge-mr` est refusé), et un
    `workflow_dispatch` tourne sur `refs/heads/<branche>` quand `pull_request` tourne sur la ref de
    MERGE. Les deux portent le même `head_sha`, donc `merge-mr` accepterait le run de dispatch sans
    broncher : il aurait vérifié la branche seule là où on croyait avoir vérifié son merge avec
    `main`. Substituer en silence une vérification plus faible est le contraire de « aucun merge non
    vérifié » (#417).
    """
    depot.pose_etat(
        graphql=[regle_pr("chore/1-x", pr=PR, sha=SHA_TETE, ferme=(1,))],
        rest=[regle_run_absent("chore/1-x")],
    )
    depot.lib("pipeline-wait", "chore/1-x", "--timeout", "600", reglages=NAISSANCE)
    assert ecritures(depot) == [], f"aucune écriture côté forge : {ecritures(depot)}"
    declenchements = [ligne for ligne in depot.appels()
                      if "workflow" in ligne or "rerun" in ligne or "dispatch" in ligne]
    assert declenchements == [], (
        f"le verbe a déclenché quelque chose : {declenchements}. Il le NOMME, il ne le fait pas."
    )


def test_merge_mr_nomme_la_naissance_dans_ses_deux_formes(depot: Depot) -> None:
    """Un seul marqueur pour deux causes, parce que l'appelant les traite pareil (#595).

    « Aucun run pour la branche » et « le dernier run porte un sha antérieur » sont deux formes
    d'une MÊME attente : le run de la tête n'est pas né. Le pilote s'en sert pour nommer l'attente
    sur sa console (`merge_naissance`), et il ne peut le faire que si les deux messages portent le
    même marqueur — « périmé », conservé parce qu'il dit la cause, ne se rapproche d'aucune des deux
    tout seul.
    """
    _mergeable(depot, run=regle_run_absent(BRANCHE))
    absent = depot.lib("merge-mr", BRANCHE)
    assert absent.returncode == 3, absent.stdout + absent.stderr
    assert "pas encore né" in absent.stderr

    # Le même décor, dont on ne change QUE le run : la branche et sa PR sont déjà en place.
    depot.pose_etat(rest=[regle_run(BRANCHE, sha=SHA_AVANT)])
    perime = depot.lib("merge-mr", BRANCHE)
    assert perime.returncode == 3, perime.stdout + perime.stderr
    assert "pas encore né" in perime.stderr
    assert "périmé" in perime.stderr, "la cause reste distinguable dans le même message"


# =====================================================================================
# `merge-order` — l'ordre le moins conflictuel, sur le graphe de #299 (#416)
# =====================================================================================
# Le graphe est celui MESURÉ le 2026-08-07 : 6 PR, 5 arêtes, un carrefour. Il est reconstruit ici
# avec de vrais commits qui se contredisent — un graphe bouchonné aurait testé le bouchon, alors
# que `merge-tree` est justement la seule source dont #303 a montré qu'elle pouvait trancher.
#
#     carrefour ── a ── b            e (isolée)
#         │  │     └────┘
#         │  ├── c
#         └───── d
#
# Le MODÈLE DE COÛT qui justifie le tri : une PR ne paie QU'UNE résolution quel que soit le nombre
# de voisines mergées avant elle (un seul `git merge origin/main` les absorbe toutes). Le coût d'un
# ordre est donc le nombre de PR ayant au moins une voisine mergée avant elles — et c'est ce que
# `_cout` mesure, plutôt que de vérifier une permutation apprise par cœur.

ARETES = {
    frozenset(("carrefour", "a")),
    frozenset(("carrefour", "b")),
    frozenset(("carrefour", "c")),
    frozenset(("carrefour", "d")),
    frozenset(("a", "b")),
}


def _cout(ordre: list[str]) -> int:
    """Le nombre de PR qui paieront une résolution si on merge dans cet ordre."""
    deja: set[str] = set()
    total = 0
    for nom in ordre:
        if any(frozenset((nom, vu)) in ARETES for vu in deja):
            total += 1
        deja.add(nom)
    return total


def _graphe(depot: Depot) -> dict[str, str]:
    """Monte les six branches. Une arête = un fichier que les deux côtés modifient différemment.

    Les fichiers sont posés sur `main` d'abord : deux branches qui modifient une base commune
    conflictent sur le CONTENU, ce qui est le cas réel — deux ajouts du même fichier conflicteraient
    aussi, mais pour une raison que le dépôt ne produit jamais.
    """
    for arete in sorted("-".join(sorted(a)) for a in ARETES):
        (depot.racine / f"{arete}.txt").write_text("base\n", encoding="utf-8", newline="\n")
    (depot.racine / "solo.txt").write_text("base\n", encoding="utf-8", newline="\n")
    depot.git("add", "-A")
    depot.git("commit", "--quiet", "-m", "chore: la base commune du graphe")
    depot.git("push", "--quiet", "origin", "main")

    branches = {}
    fichiers: dict[str, list[str]] = {"e": ["solo"]}
    for arete in ARETES:
        for nom in arete:
            fichiers.setdefault(nom, []).append("-".join(sorted(arete)))
    for nom in ("carrefour", "a", "b", "c", "d", "e"):
        branche = f"feat/{nom}"
        _branche(
            depot,
            branche,
            {f"{f}.txt": f"la version de {nom}\n" for f in fichiers[nom]},
        )
        branches[nom] = branche
    return branches


def _ordre_rendu(depot: Depot, branches: dict[str, str], entree: list[str]) -> list[str]:
    r = depot.lib("merge-order", *[branches[nom] for nom in entree])
    assert r.returncode == 0, r.stdout + r.stderr
    inverse = {v: k for k, v in branches.items()}
    return [
        inverse[ligne.split("\t")[1]]
        for ligne in r.stdout.splitlines()
        if ligne and not ligne.startswith("#")
    ]


def test_l_ordre_par_degre_croissant_coute_deux_resolutions_et_non_quatre(depot: Depot) -> None:
    """Le chiffre de #299, tenu par la mesure et non par la mémoire d'une permutation.

    Le ticket a été abandonné (2026-08-07) ; son analyse ne l'est pas, et c'est elle que ce verbe
    réutilise. Commencer par le carrefour force CHACUNE de ses voisines à payer ; le merger en
    dernier ne le fait payer qu'une fois.
    """
    branches = _graphe(depot)
    entree = ["carrefour", "a", "b", "c", "d", "e"]
    rendu = _ordre_rendu(depot, branches, entree)

    assert sorted(rendu) == sorted(entree), f"aucune branche perdue en route : {rendu}"
    assert _cout(rendu) == 2, f"l'ordre rendu ({rendu}) coûte {_cout(rendu)} résolutions"
    assert _cout(entree) == 4, (
        "l'ordre d'entrée, carrefour en tête, est bien le mauvais — sans quoi le test ne "
        "comparerait rien"
    )
    assert rendu[-1] == "carrefour", "le carrefour passe en dernier : c'est tout le tri"
    assert rendu[0] == "e", "une PR sans voisine passe en premier"


def test_le_degre_et_les_voisines_sont_rendus_avec_l_ordre(depot: Depot) -> None:
    """Le rang seul serait un verdict à croire sur parole ; le degré et les voisines le motivent."""
    branches = _graphe(depot)
    r = depot.lib("merge-order", *[branches[n] for n in ("carrefour", "a", "e")])
    assert r.returncode == 0, r.stdout + r.stderr
    lignes = {
        ligne.split("\t")[1]: ligne.split("\t")
        for ligne in r.stdout.splitlines()
        if ligne and not ligne.startswith("#")
    }
    assert r.stdout.splitlines()[0].split("\t") == ["# rang", "branche", "pr", "degre", "voisines"]
    assert lignes[branches["carrefour"]][3] == "1", (
        "le degré se compte sur la LISTE SOUMISE, pas sur le dépôt : le carrefour a quatre "
        "voisines en tout, une seule ici (a). Le compter à 4 ordonnerait un graphe non demandé"
    )
    assert lignes[branches["e"]][3] == "0"
    assert lignes[branches["e"]][4] == "-", "un champ vide décalerait toutes les colonnes suivantes"
    assert branches["a"] in lignes[branches["carrefour"]][4]


def test_une_branche_sans_ancetre_commun_n_est_pas_une_arete(depot: Depot) -> None:
    """⚠ `git merge-tree` rend `128` — et non `1` — quand le merge est impossible à ÉVALUER.

    Le compter comme un conflit gonflerait un degré, donc fausserait tout l'ordre : c'est le piège
    nommé en docs/10 §8.3, ici sur le verbe qui construit le graphe plutôt que sur celui qui le lit.
    """
    _branche(depot, "feat/ordinaire", {"a.txt": "ordinaire\n"})
    depot.git("checkout", "--quiet", "--orphan", "feat/orpheline")
    depot.git("rm", "-rq", "--cached", ".")
    (depot.racine / "orphelin.txt").write_text("sans ancêtre\n", encoding="utf-8", newline="\n")
    depot.git("add", "orphelin.txt")
    depot.git("commit", "--quiet", "-m", "feat: histoire séparée")
    depot.git("push", "--quiet", "origin", "feat/orpheline")
    depot.git("checkout", "--quiet", "--force", "main")

    r = depot.lib("merge-order", "feat/ordinaire", "feat/orpheline")
    assert r.returncode == 0, r.stdout + r.stderr
    degres = {
        ligne.split("\t")[1]: ligne.split("\t")[3]
        for ligne in r.stdout.splitlines()
        if ligne and not ligne.startswith("#")
    }
    assert degres == {"feat/ordinaire": "0", "feat/orpheline": "0"}, (
        f"une évaluation impossible n'est pas un conflit : {degres}"
    )
    assert "impossible à évaluer" in r.stderr, "l'arête ignorée est DITE, pas escamotée"


def test_merge_order_n_ecrit_rien(depot: Depot) -> None:
    """Lecture seule : ce verbe ne merge rien, ne pousse rien, n'écrit ni forge ni dépôt."""
    branches = _graphe(depot)
    avant = depot.git("rev-parse", "HEAD")
    depot.lib("merge-order", *branches.values())
    assert ecritures(depot) == []
    assert depot.git("rev-parse", "HEAD") == avant
    assert depot.git("status", "--porcelain") == "", "aucun index touché, aucune branche sortie"


def test_main_est_ecartee_de_l_ordre(depot: Depot) -> None:
    branches = _graphe(depot)
    r = depot.lib("merge-order", "main", branches["e"])
    assert r.returncode == 0, r.stdout + r.stderr
    rendues = [
        ligne.split("\t")[1] for ligne in r.stdout.splitlines()
        if ligne and not ligne.startswith("#")
    ]
    assert rendues == [branches["e"]]
    assert "n'est pas une branche de ticket" in r.stderr


# =====================================================================================
# `guard.sh` — le `deny` tient, et l'interdit a changé de forme (#417)
# =====================================================================================
# Ce que #413 a renversé n'est PAS le `deny` : c'est sa raison. « Ne jamais merger » est devenu
# « aucun merge non vérifié », et les deux se distinguent à un seul endroit observable — le message
# du refus, que lit un modèle. Un refus qui laisse croire que le merge n'a jamais lieu, alors que le
# dépôt merge désormais tout seul, envoie chercher un contournement au lieu du chemin prévu.
#
# Le hook est joué depuis le dépôt RÉEL : `--test` est du pur filtrage de motifs, sans lecture de
# dépôt ni écriture, et c'est le fichier versionné — celui qui protège les runs — qu'on veut juger.


def _guard(*args: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(  # noqa: S603
        [BASH, str(RACINE / "scripts/orchestrate/guard.sh"), *args],
        cwd=str(RACINE), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )


@pytest.mark.parametrize(
    "commande",
    [
        "gh pr merge 42",
        "gh pr merge 42 --squash --delete-branch",
        "gh pr merge --auto 42",
        "cd /tmp && gh pr merge 42",
    ],
)
def test_le_merge_nu_reste_refuse(commande: str) -> None:
    """Lever ce `deny` mettrait un merge au rouge à un `gh` près, pour zéro gain.

    Aucun appelant légitime n'a besoin de la commande nue : `merge-mr` passe par l'API REST, donc le
    geste vérifié ne traverse jamais ce motif.
    """
    r = _guard("--test", commande)
    assert r.returncode == 2, f"« {commande} » aurait dû être refusé : {r.stdout}"
    assert "REFUSÉ" in r.stdout


def test_le_refus_du_merge_nu_nomme_le_chemin_verifie() -> None:
    """Le contenu de #417, et la seule chose que le `deny` seul ne peut pas porter.

    Le message doit dire les DEUX moitiés : ce qui est interdit (le merge nu) et ce qui ne l'est
    plus (le merge vérifié). Un « ne merge jamais » resté en place ferait chercher un contournement
    à une session qui n'a qu'à appeler le bon verbe.
    """
    r = _guard("--test", "gh pr merge 42")
    assert "merge-mr" in r.stdout, "le chemin de remplacement est NOMMÉ, pas sous-entendu"
    assert "pas le merge" in r.stdout, "l'interdit porte sur le merge NON VÉRIFIÉ, pas sur le merge"
    assert "jamais merger" not in r.stdout


def test_le_deny_du_depot_est_toujours_recopie_dans_le_hook() -> None:
    """`--check` : chaque règle `deny` de `.claude/settings.json` est aussi refusée par le hook.

    Les deux filets jugent le TEXTE de la commande que la session lance, jamais ce qu'un script
    appelle en interne — c'est ce qui laisse le geste nu impossible pendant que le geste vérifié
    passe. Le jour où l'un des deux tombe, l'autre ne le dirait pas tout seul.
    """
    r = _guard("--check")
    assert r.returncode == 0, r.stdout + r.stderr


# --- Aucun prompt ne prescrit le geste nu ---------------------------------------------------------
# ⚠ CE QUI EST CHERCHÉ EST UNE PRESCRIPTION, PAS UNE MENTION. Ce dépôt NOMME `gh pr merge` une
# dizaine de fois — pour l'interdire, pour expliquer pourquoi le `deny` reste en place, pour dire
# par quoi il est remplacé. Compter ces lignes-là pour des fautes rendrait le test impossible à
# satisfaire autrement qu'en effaçant la mémoire du chantier, ce qui est l'inverse du but.
#
# Une prescription se reconnaît à sa FORME D'APPEL : la commande suivie d'un argument (un numéro,
# un drapeau, une variable, un gabarit), ou seule en tête de ligne comme dans un bloc de code. Les
# mentions du dépôt, elles, sont toutes suivies d'un guillemet fermant, d'un `/` ou d'un `:`.

_PRESCRIT = re.compile(
    r"""(?:^|[\s`"'(>|-])gh\s+pr\s+merge(?:\s+(?:--?\w|\d|\$|<[a-z]|"|')|\s*`?\s*$)"""
)


def test_aucun_prompt_ne_prescrit_le_merge_nu() -> None:
    """Le critère du lot #417, gardé par un `grep` parce que c'est ce qu'il promet.

    Une seule ligne de prompt qui prescrirait le geste nu vaudrait plus que le `deny` : le refus
    arriverait après coup, sur une session partie faire ce qu'on lui a écrit de faire.
    """
    # Le motif prouve sa capacité à TROUVER avant de balayer — sans quoi un `grep` qui ne cherche
    # rien rend un ✓ sur une question jamais posée.
    for fautif in (
        "Puis merge la PR : `gh pr merge 42 --squash`",
        "gh pr merge --auto",
        '    gh pr merge "$PR" --delete-branch',
        "- `gh pr merge <numéro>`",
        "```\ngh pr merge\n```".splitlines()[1],
    ):
        assert _PRESCRIT.search(fautif), f"le motif ne trouve pas la prescription : {fautif!r}"
    # …et sa capacité à LAISSER PASSER les mentions réelles du dépôt, qui sont son objet même.
    for licite in (
        "Jamais `gh pr merge` : le geste **nu** reste en `deny` côté permissions",
        "ni `gh pr merge`/`close`/`review`/`edit`, ni `gh pr ready`",
        '      "Bash(gh pr merge:*)",',
        "« gh pr merge » reste refusé, cf. docs/10 §6",
        "  if printf '%s' \"$cmd\" | grep -qE 'gh[[:space:]]+pr[[:space:]]+merge'; then",
    ):
        assert not _PRESCRIT.search(licite), f"mention comptée pour une faute : {licite!r}"

    fautifs = []
    for dossier in (RACINE / ".claude", RACINE / "scripts"):
        for fichier in sorted(dossier.rglob("*")):
            if "__pycache__" in fichier.parts or not fichier.is_file():
                continue
            script = fichier.suffix == ".sh"
            texte = fichier.read_text(encoding="utf-8", errors="replace")
            for numero, ligne in enumerate(texte.splitlines(), start=1):
                # ⚠ Un COMMENTAIRE de script est hors de portée, et c'est une borne et non un trou :
                # `lib.sh` explique en commentaire pourquoi `gh pr merge --auto` n'est pas la voie
                # retenue (il merge hors du seul chemin de merge du dépôt, et reste aveugle au sha
                # qui porte le vert — §8.8) — c'est exactement la mémoire que ce chantier demande
                # d'écrire, et rien ne l'exécute.
                # Les PROMPTS embarqués dans un script (`run.sh`, qui compose ce que lit une
                # session) ne sont pas des commentaires : ils restent, eux, intégralement balayés.
                if script and ligne.lstrip().startswith("#"):
                    continue
                if _PRESCRIT.search(ligne):
                    fautifs.append(f"{fichier.relative_to(RACINE)}:{numero} : {ligne.strip()[:90]}")
    assert not fautifs, (
        "le geste nu est prescrit quelque part — il doit passer par « lib.sh merge-mr » :\n"
        + "\n".join(fautifs)
    )


# =====================================================================================
# La clôture interactive débloque ce qui est réparable (#460)
# =====================================================================================
# Le déblocage automatique existait déjà, mais D'UN SEUL CÔTÉ : le pilote d'un run ouvre une session
# `/mr-fix` sur un `4`/`5` depuis #420, pendant qu'en clôture interactive la même cause était
# seulement PROPOSÉE à un humain. #460 aligne les deux appelants sur la commande qu'ils partagent.
#
# Ce qui se garde ici est un PROMPT, donc du texte — mais pas n'importe lequel : la conduite par
# verdict est ce qui décide entre « réparer », « repasser » et « laisser à un humain », et une
# conduite qui glisserait d'un code à l'autre ne se verrait nulle part ailleurs. Les blocs sont donc
# découpés par leur NUMÉROTATION (étape, sous-étape, puce de verdict), jamais par une phrase : c'est
# la structure qu'on veut garder stable, pas une formulation.

COMMANDES = RACINE / ".claude" / "commands"


def _prompt(nom: str) -> str:
    fichier = COMMANDES / nom
    assert fichier.is_file(), f"{fichier} introuvable"
    return fichier.read_text(encoding="utf-8")


def _etape(texte: str, numero: int) -> str:
    """L'étape numérotée `numero` d'un prompt de commande, jusqu'à la suivante."""
    debut = re.search(rf"^{numero}\. ", texte, re.M)
    assert debut, f"étape {numero} introuvable — la numérotation du prompt a changé"
    reste = texte[debut.start() :]
    suite = re.search(rf"^{numero + 1}\. ", reste, re.M)
    return reste[: suite.start()] if suite else reste


def _sous_etape(etape: str, numero: int) -> str:
    """La sous-étape indentée `numero.` d'une étape, jusqu'à la suivante."""
    debut = re.search(rf"^ +{numero}\. ", etape, re.M)
    assert debut, f"sous-étape {numero} introuvable"
    reste = etape[debut.start() :]
    suite = re.search(rf"^ +{numero + 1}\. ", reste, re.M)
    return reste[: suite.start()] if suite else reste


def _verdicts(sous_etape: str) -> dict[str, str]:
    """Les puces « - `N` → … », indexées par CHAQUE code que leur ligne d'ouverture cite.

    Un même bloc peut répondre pour deux codes — c'est le cas de `4`/`5` depuis #460, qui partagent
    une conduite parce qu'ils partagent une cause : réparable. Indexer par code plutôt que par puce
    est ce qui permet de demander « que dit le prompt du verdict N ? » sans présumer de la mise en
    forme du jour.
    """
    ouvertures = list(re.finditer(r"^ *- +(`\d`.*)$", sous_etape, re.M))
    assert ouvertures, "aucune puce de verdict — le découpage du prompt a changé de forme"
    blocs: dict[str, str] = {}
    for rang, ouverture in enumerate(ouvertures):
        fin = ouvertures[rang + 1].start() if rang + 1 < len(ouvertures) else len(sous_etape)
        bloc = sous_etape[ouverture.start() : fin]
        for code in re.findall(r"`(\d)`", ouverture.group(1)):
            blocs[code] = bloc
    return blocs


def _verdicts_de_cloture() -> dict[str, str]:
    """Les six conduites de `merge-mr` telles que `/ticket-finish` les tient (étape 13.2)."""
    blocs = _verdicts(_sous_etape(_etape(_prompt("ticket-finish.md"), 13), 2))
    manquants = {"0", "1", "2", "3", "4", "5", "6"} - blocs.keys()
    assert not manquants, f"des verdicts de merge-mr ne sont plus tenus : {sorted(manquants)}"
    return blocs


# Une PROPOSITION se reconnaît à ce qu'elle laisse la main : « propose /mr-fix », « tu peux lancer
# /mr-fix ». C'est le geste que #460 remplace, et le distinguer d'une simple MENTION de la commande
# est tout l'objet du motif — le bloc réécrit nomme `/mr-fix` à chaque ligne.
_PROPOSITION = re.compile(r"propos\w+[^.\n]{0,24}`?/mr-fix")

# Un DÉCLENCHEMENT route vers la commande : « enchaîne sur /mr-fix », « lance /mr-fix ». Une
# INTERDICTION emploie les MÊMES verbes pour dire le contraire — « n'enchaîne JAMAIS sur /mr-fix » —
# et depuis #595 le bloc `3` en porte une, précisément pour la conduite que ce test garde. Un motif
# qui ne lit que le nom de la commande rougit donc sur le texte qui LUI donne raison : c'est la
# distinction usage/mention du dépôt (#365, #599), et elle se tient ici au verbe et à sa négation.
# En français la négation précède le verbe (« ne »/« n' »), ce qui suffit à les séparer.
_DECLENCHEMENT = re.compile(
    r"(?<!\bne )(?<!\bn')(?:enchaîn|lanc|invoqu|propos|déclench)\w*[^.\n]{0,32}`?/mr-fix",
    re.I,
)


def test_sur_une_cause_reparable_la_cloture_enchaine_au_lieu_de_proposer() -> None:
    """Le critère du ticket : sur `4` et `5`, on répare sans demander.

    Ces deux verdicts sont les seuls des six à nommer un correctif plutôt qu'une décision. Les
    laisser à un geste manuel revenait à traiter la même cause de deux façons selon l'appelant —
    d'office en run (#420), à la main ici — alors que `/mr-fix` est la MÊME commande.
    """
    # Le motif prouve d'abord qu'il sait trouver ce qu'il cherche : sans ça, un ✓ ne dirait rien.
    for fautif in (
        "propose `/mr-fix <numéro>`.",
        "PR ouverte, ticket « En revue », propose /mr-fix",
        "tu peux proposer `/mr-fix` à l'utilisateur",
    ):
        assert _PROPOSITION.search(fautif), f"le motif ne voit pas la proposition : {fautif!r}"

    blocs = _verdicts_de_cloture()
    for code in ("4", "5"):
        bloc = blocs[code]
        assert "/mr-fix" in bloc, f"le verdict {code} ne nomme plus la commande de remédiation"
        assert "sans demander" in bloc, (
            f"le verdict {code} doit enchaîner D'OFFICE : « sans demander » est le mot qui sépare "
            "l'enchaînement de la proposition qu'il remplace"
        )
        assert not _PROPOSITION.search(bloc), (
            f"le verdict {code} propose encore `/mr-fix` au lieu de l'enchaîner"
        )
        # …et le motif du test voisin se prouve ici sur le VRAI texte : c'est le seul endroit du
        # prompt qui prescrit `/mr-fix`, donc s'il ne le voit pas, son « aucun » ne dit plus rien.
        assert _DECLENCHEMENT.search(bloc), (
            f"le verdict {code} ne route plus vers `/mr-fix` — ou le motif a cessé de le voir"
        )
    # `4` et `5` partagent la même conduite : le découpage doit le rendre visible, sans quoi la
    # moitié du critère pourrait tomber sans que rien ne rougisse.
    assert blocs["4"] == blocs["5"], "les deux causes réparables ne partagent plus leur conduite"


def test_les_autres_verdicts_ne_declenchent_aucun_deblocage() -> None:
    """Le second critère, et le plus facile à perdre : ce qui ne se répare pas ne s'envoie pas.

    Un `6` est un geste humain PAR DÉFINITION (#415) — lui envoyer une remédiation ferait payer une
    session entière pour qu'elle reconfirme qu'elle ne peut rien. Un `3` n'a pas de correctif à
    écrire : il attend un verdict, et sa reprise unique lui suffit.

    Ce qui est gardé est le DÉCLENCHEMENT, jamais le nom de la commande : depuis #595 le bloc `3`
    interdit `/mr-fix` en toutes lettres, parce qu'une PR dont le pipeline n'est pas encore né n'a
    ni conflit ni job rouge. Exiger l'absence du mot ferait rougir l'interdiction en même temps que
    la prescription — et le remède serait de retirer la garde, c'est-à-dire l'inverse du critère.
    """
    # Le motif prouve d'abord son motif : il voit la prescription, et laisse passer l'interdiction.
    for fautif in (
        "sur un `3`, enchaîne sur `/mr-fix <numéro>`",
        "lance `/mr-fix` et reprends",
        "invoque /mr-fix, elle est autosuffisante",
    ):
        assert _DECLENCHEMENT.search(fautif), f"le motif ne voit pas le déclenchement : {fautif!r}"
    for licite in (
        "⚠ **N'enchaîne JAMAIS sur `/mr-fix` ici** (#595)",
        "rien que `/mr-fix` sache réparer",
        "ne lance pas `/mr-fix` : il n'y a ni conflit ni job rouge",
    ):
        assert not _DECLENCHEMENT.search(licite), f"le motif confond mention et usage : {licite!r}"

    blocs = _verdicts_de_cloture()
    for code in ("0", "1", "2", "3", "6"):
        assert not _DECLENCHEMENT.search(blocs[code]), (
            f"le verdict {code} déclenche un déblocage qu'il ne devrait pas — "
            "seuls `4` et `5` sont réparables"
        )
    assert "pas plus" in blocs["3"], "le `3` a perdu sa reprise UNIQUE (pipeline-wait + merge-mr)"
    assert "geste humain" in blocs["6"] or "anomalie" in blocs["6"]


def test_le_plafond_du_deblocage_est_borne_et_aligne_sur_celui_du_run() -> None:
    """« Borné », et borné sur la même valeur que le run — sans partager son réglage.

    Le prompt écrit son plafond en toutes lettres : une clôture interactive ne lit aucune variable
    d'environnement de pilote. C'est donc ICI que les deux valeurs peuvent diverger en silence, et
    ce test les compare à la SOURCE plutôt qu'à une constante recopiée : le jour où le run change
    de plafond, la question « et la clôture ? » est posée au lieu d'être ignorée.
    """
    deblocage = _sous_etape(_etape(_prompt("ticket-finish.md"), 13), 3)
    assert "deux tentatives" in deblocage.lower(), "le déblocage n'annonce plus son plafond"

    run = (RACINE / "scripts/orchestrate/run.sh").read_text(encoding="utf-8")
    plafond = re.search(r'MRFIX_MAX="\$\{MAESTRO_ORCHESTRATE_MRFIX_MAX:-(\d+)\}"', run)
    assert plafond, "le plafond du run est introuvable — a-t-il changé de nom ?"
    assert plafond.group(1) == "2", (
        f"le run borne à {plafond.group(1)} sessions et la clôture à deux : les aligner, ou dire "
        "pourquoi ils diffèrent"
    )
    # …et la variable est nommée comme celle du RUN, jamais comme un réglage de la clôture.
    assert "MAESTRO_ORCHESTRATE_MRFIX_MAX" in deblocage, (
        "le prompt doit nommer la variable du run pour qu'on ne la croie pas sienne"
    )
    assert "run" in deblocage.split("MAESTRO_ORCHESTRATE_MRFIX_MAX")[1][:200]


def test_la_cloture_ne_repasse_pas_merge_mr_apres_le_deblocage() -> None:
    """Relire le verdict après `/mr-fix` n'ajoute aucune vérification, et en invente une fausse.

    Son étape 12 EST l'appel à `merge-mr` : sur une PR qu'il vient de merger, le verbe rendrait `6`
    (« PR fermée »), c'est-à-dire une anomalie fabriquée par la relecture elle-même — le genre de
    faux verdict que tout ce chantier existe pour supprimer.
    """
    deblocage = _sous_etape(_etape(_prompt("ticket-finish.md"), 13), 3)
    assert "Ne repasse pas `merge-mr`" in deblocage, (
        "le déblocage doit interdire de relire le verdict après `/mr-fix` — sans quoi la clôture "
        "rendrait une anomalie sur une PR qu'elle vient de merger"
    )
    assert "`6`" in deblocage, "la conséquence du repassage (un `6` inventé) doit être nommée"


def test_le_resume_de_cloture_rend_le_deblocage_sur_sa_propre_ligne() -> None:
    """Le troisième critère : jamais un ✅ global, et « non tenté » ≠ « refusé ».

    C'est la distinction que #303 a établie pour `/mr-fix`, et elle vaut mot pour mot ici : « non
    tenté » est la conséquence d'un abandon de la remédiation, « refusé » un verdict sur la PR. Les
    confondre ferait chercher un problème de PR là où il y a une remédiation inachevée.
    """
    resume = _etape(_prompt("ticket-finish.md"), 15)
    lignes = [ligne for ligne in resume.splitlines() if ligne.lstrip().startswith("| **")]
    deblocage = [ligne for ligne in lignes if "**Déblocage**" in ligne]
    assert len(deblocage) == 1, (
        "le résumé doit porter UNE ligne « Déblocage », distincte de celle du merge : "
        f"{[ligne.strip()[:60] for ligne in lignes]}"
    )
    for issue in ("non tenté", "abouti", "sans succès"):
        assert issue in deblocage[0], f"l'issue « {issue} » manque à la ligne du déblocage"
    assert "« Non tenté » et « refusé »" in resume
    assert "Jamais de ✅ global" in resume


def test_une_session_de_run_ne_debloque_pas_elle_meme() -> None:
    """En run, le déblocage est au pilote — et la porte est fermée en dur, pas seulement dite.

    Une session qui lancerait `/mr-fix` d'elle-même ferait tourner deux remédiations sur la même PR
    et attendrait un pipeline sur le quota du run : les deux choses que #419 refuse. Le prompt le
    dit AVANT que la session s'y heurte, et `guard.sh` le tient quoi qu'il arrive.
    """
    deblocage = _sous_etape(_etape(_prompt("ticket-finish.md"), 13), 3)
    assert "En run autonome, n'enchaîne rien" in deblocage, (
        "le déblocage doit s'exclure lui-même d'un run — sinon une session le tenterait en "
        "concurrence du pilote, sur le quota du run"
    )
    assert "guard.sh" in deblocage and "pilote" in deblocage, (
        "l'exclusion doit nommer QUI débloque en run (le pilote) et ce qui la tient (`guard.sh`) : "
        "un ordre contredit sans explication se contourne au lieu de se suivre"
    )

    # Le refus est réel : c'est lui qui rend ce verdict INATTEIGNABLE en run, l'attente venant
    # avant le merge. Sans ce maillon, la consigne du prompt serait la seule barrière.
    r = _guard("--test", "bash scripts/gitlab/lib.sh pipeline-wait main")
    assert r.returncode == 2, f"l'attente de pipeline devrait être refusée en run : {r.stdout}"
    assert "pilote" in r.stdout


def test_ticket_ship_annonce_le_deblocage_sans_le_reimplementer() -> None:
    """`/ticket-ship` délègue tout à `/ticket-finish` — le déblocage compris, et son attente avec.

    Ce qu'il doit ajouter n'est pas une étape mais un AVERTISSEMENT : la commande peut désormais
    attendre un pipeline de plus. #418 a choisi d'annoncer ces attentes plutôt que de les masquer,
    et une commande « zéro friction » qui ne rend pas la main pendant dix minutes sans avoir
    prévenu passe pour bloquée.
    """
    ship = _prompt("ticket-ship.md")
    assert "/mr-fix" in ship and "#460" in ship
    assert "Ne ré-implémente aucune de ces étapes ici" in ship
    # Le déblocage ne se décrit pas comme une étape de `/ticket-ship` : la commande qui l'exécute
    # est nommée, et c'est elle qui en porte les règles.
    delegation = _etape(ship, 7)
    assert "/mr-fix" in delegation and "/ticket-finish" in delegation


# =====================================================================================
# Le worktree se ramasse dans la session qui merge (#519)
# =====================================================================================
# Le ménage d'après-merge existait déjà, mais D'UN SEUL CÔTÉ lui aussi : le pilote d'un run retire
# worktree et branche sur le verdict `0` depuis #438, pendant qu'en clôture interactive ils
# attendaient le PROCHAIN `/ticket-start`. La raison n'était pas un choix de conception mais une
# contrainte de position — `gc` refuse de retirer le worktree de la session qui l'appelle —, et
# `ExitWorktree` la lève en replaçant la session dans le clone principal.
#
# Ce qui se garde ici est encore un PROMPT, pour la raison de la section précédente : aucun code
# n'a été écrit (`gc --iid` et `cleanup-merged --auto` existent depuis #438 et #305, et leur
# mécanique est gardée par `test_worktree.py`). Ce qui peut se perdre est donc ce que la commande
# PRESCRIT — les deux verbes, leur ordre, le verdict qui les déclenche, et les contournements
# qu'elle s'interdit.


def _aplati(texte: str) -> str:
    """Le texte sur une seule ligne, espaces normalisés.

    Les prompts sont repliés à 100 colonnes : chercher « A … B » à quelques mots de distance y
    échouerait une fois sur deux selon l'endroit où tombe le retour à la ligne. Aplatir d'abord
    fait porter le motif sur la phrase plutôt que sur sa mise en page.
    """
    return re.sub(r"\s+", " ", texte)


def _ramassage() -> str:
    """L'étape de ramassage de `/ticket-finish` — celle qui suit le merge et précède le résumé."""
    etape = _etape(_prompt("ticket-finish.md"), 14)
    assert "ExitWorktree" in etape, (
        "l'étape 14 n'est pas celle du ramassage — la numérotation du prompt a changé"
    )
    return etape


def test_sur_un_merge_reussi_la_cloture_ramasse_son_worktree() -> None:
    """Le premier critère : sortir du worktree, puis les deux verbes du pilote, dans son ordre.

    L'ORDRE est le seul point non négociable des deux : `git branch -D` refuse une branche encore
    empruntée par un worktree (#305), donc purger avant d'avoir retiré ne purgerait jamais rien —
    et l'inversion serait silencieuse, la clôture continuant comme si de rien n'était.
    """
    etape = _aplati(_ramassage())
    sortie = etape.find("ExitWorktree")
    gc = etape.find("worktree.sh gc --iid")
    purge = etape.find("cleanup-merged --auto")
    assert gc > 0, "le retrait du worktree doit passer par `worktree.sh gc --iid`"
    assert purge > 0, "la purge de la branche doit passer par `lib.sh cleanup-merged --auto`"
    assert sortie < gc < purge, (
        "l'ordre prescrit doit être : sortir du worktree, retirer le worktree, purger la branche — "
        f"trouvé ExitWorktree={sortie}, gc={gc}, cleanup-merged={purge}"
    )


def test_le_ramassage_ne_se_declenche_que_sur_un_merge_reussi() -> None:
    """Le deuxième critère : sur `3`/`4`/`5`/`6`, rien n'est retiré.

    Ces quatre verdicts laissent la PR ouverte, donc un travail qui vit encore dans ce worktree.
    Un ménage qui s'y déclencherait retirerait le répertoire de travail d'un ticket non livré —
    exactement le dommage que la garde de `gc` existe pour empêcher, mais par le mauvais bout.
    """
    etape = _aplati(_ramassage())
    assert re.search(r"sur\s+`0`\s+seulement", etape), (
        "l'étape doit dire dans son titre que seul le verdict `0` la déclenche"
    )
    assert re.search(r"[Nn]'entreprends rien sur `3`", etape), (
        "les quatre autres verdicts doivent être nommés et écartés explicitement"
    )
    # Et le bloc du verdict `0` renvoie ici : sans ce fil, une session lirait le merge sans jamais
    # apprendre qu'une étape lui succède.
    assert "étape 14" in _aplati(_verdicts_de_cloture()["0"]), (
        "le verdict `0` doit renvoyer à l'étape de ramassage — c'est lui qui la déclenche"
    )


def test_le_ramassage_ne_court_circuite_aucun_garde_fou() -> None:
    """Le troisième critère : `ExitWorktree` sort, il ne nettoie pas.

    `action: "remove"` est refusé pour deux raisons indépendantes, et la seconde suffirait seule :
    le tool ne connaît ni la confirmation du merge par la forge (#197), ni la mesure du travail non
    sauvegardé contre le sha de merge (#438), ni la pose de « Terminé » (#275), ni le rattrapage
    des coquilles (#422). On sort du worktree avec `ExitWorktree`, on nettoie avec les verbes du
    dépôt.
    """
    prescrit = re.compile(r"(?<!jamais )`?action: \"remove\"")
    # Le motif prouve d'abord qu'il sait trouver ce qu'il cherche — sinon un ✓ ne dirait rien.
    for fautif in (
        'appelle `ExitWorktree` avec `action: "remove"`',
        'sors du worktree : `action: "remove"`',
    ):
        assert prescrit.search(_aplati(fautif)), f"le motif rate un cas fautif : {fautif}"

    etape = _aplati(_ramassage())
    assert 'action: "keep"' in etape, "la sortie doit se faire en `action: \"keep\"`"
    for occurrence in re.finditer(r'action: "remove"', etape):
        amont = etape[max(0, occurrence.start() - 40) : occurrence.start()]
        assert re.search(r"[Jj]amais|pas\b|refus", amont), (
            "`action: \"remove\"` n'apparaît que pour être interdit, jamais pour être employé : "
            f"…{amont}"
        )

    # La garde du travail non sauvegardé est celle de `gc`, et elle n'est ni contournée…
    # Ce qu'une session LANCE tient dans les blocs de code : le corps du texte, lui, cite
    # `git branch -D` pour expliquer POURQUOI l'ordre compte, et confondre les deux rendrait le
    # test rouge sur la phrase qui justifie la règle qu'il garde.
    lance = "\n".join(re.findall(r"^ *```\n(.*?)^ *```", _ramassage(), re.M | re.S))
    for fautif in ("bash scripts/git/worktree.sh remove 519 --force\n", "git branch -D feat/519\n"):
        assert re.search(r"worktree\.sh remove|git branch -D|--force", fautif), (
            f"le motif rate un cas fautif : {fautif}"
        )
    for contournement in ("worktree.sh remove", "git branch -D", "--force"):
        assert contournement not in lance, (
            f"le ramassage ne doit lancer aucun contournement de `gc` — trouvé : {contournement}"
        )
    # …ni doublée par une vérification maison : deux formules qui divergeraient se remarqueraient
    # trop tard, et c'est la garde qui perdrait.
    assert re.search(r"non sauvegardé", etape), (
        "l'étape doit nommer ce que `gc` conserve, pour que l'abstention se relaie au lieu de "
        "passer pour une panne"
    )


# La règle PÉRIMÉE ne disait pas « conservé » (ce que `gc` fait toujours d'un worktree porteur de
# travail) mais « conservé EN ATTENDANT un autre geste » : c'est le report qui a disparu, pas la
# conservation. Le motif vise donc le couple, jamais le mot seul.
_MENAGE_DIFFERE = re.compile(
    r"(conservés|restent|resteront|partiront)[^.]{0,120}(/ticket-start|/branch-cleanup)"
)


def test_plus_aucun_prompt_n_annonce_un_menage_reporte() -> None:
    """Le quatrième critère, côté prompts : une seule version de la règle en vigueur.

    Deux prompts qui se contredisent ne se départagent pas : c'est celui que la session lit en
    dernier qui l'emporte, donc le hasard. La règle d'hier — « ils partiront au prochain
    `/ticket-start` » — doit avoir disparu, pas cohabiter avec la nouvelle.
    """
    for fautif in (
        "worktree et branche locale conservés (session en cours dedans) : ils partiront au "
        "prochain `/ticket-start`",
        "la locale et le worktree, eux, restent — ils partiront avec `/branch-cleanup`",
    ):
        assert _MENAGE_DIFFERE.search(_aplati(fautif)), f"le motif rate un cas fautif : {fautif}"

    fautifs = [
        f"{fichier.name} : …{trouve.group(0)[:80]}…"
        for fichier in sorted(COMMANDES.glob("*.md"))
        for trouve in [_MENAGE_DIFFERE.search(_aplati(fichier.read_text(encoding="utf-8")))]
        if trouve
    ]
    assert not fautifs, (
        "un prompt annonce encore un ménage reporté à un autre geste (#519) :\n"
        + "\n".join(fautifs)
    )


def test_le_resume_de_cloture_rend_ce_qui_a_ete_ramasse() -> None:
    """Le premier critère, seconde moitié : le résumé rend le retrait, ou la cause de l'abstention.

    Une abstention de `gc` n'est pas un échec de la clôture — c'est un travail que personne
    n'attend plus là, et le seul endroit où l'information peut encore atteindre quelqu'un.
    """
    resume = _aplati(_etape(_prompt("ticket-finish.md"), 15))
    merge = [
        ligne for ligne in _etape(_prompt("ticket-finish.md"), 15).splitlines()
        if ligne.lstrip().startswith("| **Mergé**")
    ]
    assert len(merge) == 1, "le tableau de résumé doit porter une ligne « Mergé »"
    assert "worktree" in merge[0], (
        "la ligne « Mergé » doit dire ce qu'il est advenu du worktree — c'est elle qui annonçait "
        "sa conservation"
    )
    assert "clone principal" in resume, (
        "le résumé doit dire où la session se trouve désormais : finir ailleurs qu'on a commencé "
        "est le seul effet de bord visible du ticket"
    )


def test_le_ramassage_de_la_cloture_et_celui_du_pilote_sont_exclusifs() -> None:
    """Aucun drapeau à tenir d'accord : en run, le verdict `0` n'est jamais atteint.

    `guard.sh` refuse `pipeline-wait` ET `merge-mr` à toute session de run (#419), donc la clôture
    s'y arrête avant le merge et cette étape ne s'y joue pas. C'est le pilote qui ramasse après son
    propre merge (#438). L'exclusion est par construction — encore faut-il que le prompt la dise,
    sans quoi une session tenterait le ménage d'un worktree que le pilote occupe.
    """
    etape = _aplati(_ramassage())
    assert "run autonome" in etape and "guard.sh" in etape, (
        "l'étape doit s'exclure elle-même d'un run, et nommer ce qui la tient"
    )
    assert "#438" in etape, "et nommer qui ramasse à sa place là-bas — le pilote"

    # Le refus est réel, pas seulement écrit : c'est lui qui rend le verdict `0` inatteignable.
    r = _guard("--test", "bash scripts/gitlab/lib.sh merge-mr 519")
    assert r.returncode == 2, f"le merge devrait être refusé en session de run : {r.stdout}"


def test_exitworktree_est_autorise_nu_comme_enterworktree() -> None:
    """Le cinquième critère : sans lui, la clôture s'interrompt sur son tout dernier geste.

    C'est le défaut que #199 avait corrigé à l'autre bout du cycle, pour `EnterWorktree`. Et comme
    lui, `ExitWorktree` ne déclare aucun `ruleContentField` (mesuré le 2026-08-25, #520) : une
    règle paramétrée — `ExitWorktree(keep)` — ne matcherait rien du tout, silencieusement.
    """
    reglages = json.loads((RACINE / ".claude" / "settings.json").read_text(encoding="utf-8"))
    allow = reglages["permissions"]["allow"]
    assert "ExitWorktree" in allow, (
        "`ExitWorktree` doit être dans l'`allow` du dépôt — sinon la clôture demande une "
        "autorisation qu'une session autonome n'a personne pour donner"
    )
    assert "EnterWorktree" in allow, "le pendant d'entrée ne doit pas avoir disparu (#199)"
    parametrees = [regle for regle in allow if regle.startswith("ExitWorktree(")]
    assert not parametrees, (
        f"une règle paramétrée ne matcherait rien : {parametrees} — poser le tool NU, comme "
        "`EnterWorktree` et `Skill`"
    )


def test_ticket_ship_herite_du_ramassage_sans_le_reimplementer() -> None:
    """`/ticket-ship` délègue le merge, donc il délègue ce qui le suit.

    Il n'a rien à rejouer — mais son résumé, lui, est le sien : une commande « zéro friction » qui
    finit dans un autre répertoire que celui où elle a commencé doit le dire, sans quoi la
    surprise se découvre au premier chemin relatif qui ne résout plus.
    """
    ship = _prompt("ticket-ship.md")
    assert "#519" in ship
    aplati = _aplati(ship)
    assert "clone principal" in aplati, (
        "le résumé de `/ticket-ship` doit dire où la session se trouve à la fin"
    )
    for verbe in ("worktree.sh gc --iid", "cleanup-merged --auto", "ExitWorktree"):
        assert verbe not in aplati, (
            f"`/ticket-ship` ne doit pas ré-implémenter le ramassage — trouvé : {verbe}"
        )


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# La protection de branche, posée le 2026-08-28 (#734)
#
# Elle a été ÉCRITE SANS ÊTRE JOUÉE de #338 au 2026-08-27 — indisponible sur un dépôt privé d'un
# compte Free —, et le passage du dépôt en public l'a rendue posable. Ce changement d'état déplace
# ce qu'il faut garder : tant que rien n'était posé, un décalage entre `ci.yml` et `protect-main.sh`
# ne coûtait rien ; il coûte désormais une PR INDÉFINIMENT NON MERGEABLE, que personne ne peut
# débloquer d'un clic — c'est le piège que docs/10 §8.8 et `ci.yml` nomment tous les deux, et qui
# n'avait jusqu'ici aucun gardien.
#
# Les deux tests ci-dessous sont du TEXTE et ne touchent ni réseau ni forge : ils lisent les deux
# fichiers du dépôt et comparent. C'est voulu — la question posée n'est pas « GitHub accepte-t-il ce
# corps ? » mais « ces deux listes disent-elles la même chose ? », qui se répond hors ligne.


def _jobs_de_ci_yml() -> list[str]:
    """Les noms de job de `.github/workflows/ci.yml`, lus à l'indentation du bloc `jobs:`.

    Volontairement naïf — deux espaces, un nom, `:` — parce que c'est exactement la forme sous
    laquelle GitHub rapporte un nom de check, et qu'un parseur YAML complet dirait la même chose en
    ajoutant une dépendance à une suite qui n'en a aucune.
    """
    texte = (RACINE / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    dans_jobs = False
    jobs: list[str] = []
    for ligne in texte.splitlines():
        if ligne.startswith("jobs:"):
            dans_jobs = True
            continue
        if dans_jobs and ligne and not ligne.startswith((" ", "#")):
            break  # un bloc de premier niveau après `jobs:` : on en est sorti
        trouve = re.match(r"^  ([a-zA-Z0-9_-]+):\s*$", ligne)
        if dans_jobs and trouve:
            jobs.append(trouve.group(1))
    return jobs


def test_les_checks_requis_sont_exactement_les_jobs_de_ci_yml() -> None:
    """Un job renommé d'un seul côté rend TOUTE PR non mergeable, et rien ne le dirait.

    Le check requis ne serait plus jamais rapporté sous son ancien nom : la PR attend un verdict qui
    n'arrivera pas, et aucun clic ne la débloque. Symétriquement, un job **ajouté** à `ci.yml` sans
    être requis serait un contrôle qu'on croit bloquant et qui ne bloque rien — l'inverse, et tout
    aussi silencieux. D'où une **égalité**, et non une inclusion.
    """
    script = (RACINE / "scripts" / "github" / "protect-main.sh").read_text(encoding="utf-8")
    declaration = re.search(r"^CHECKS=\(([^)]*)\)", script, re.MULTILINE)
    assert declaration, "la liste CHECKS a disparu de protect-main.sh"
    checks = declaration.group(1).split()
    jobs = _jobs_de_ci_yml()

    # Le motif prouve d'abord qu'il a trouvé quelque chose : deux listes vides satisferaient
    # l'égalité sans avoir rien vérifié.
    assert len(jobs) >= 5, f"lecture de ci.yml suspecte — {jobs}"
    assert sorted(checks) == sorted(jobs), (
        "les checks requis et les jobs de ci.yml ont divergé — toute PR deviendrait non "
        f"mergeable.\n  requis : {sorted(checks)}\n  jobs   : {sorted(jobs)}"
    )


def test_la_protection_est_opposable_au_proprietaire() -> None:
    """`enforce_admins: true` — sans quoi la protection ne couvre pas le seul compte qui merge.

    La valeur était `false` pour laisser vivre le miroir push depuis GitLab, parti avec #343/#344.
    La remettre à `false` rendrait la protection inopposable à l'administrateur, c'est-à-dire à tout
    le monde sur ce dépôt : elle ne garderait plus que ce que `merge-mr` garde déjà, et le gain de
    #734 — couvrir ce qui NE PASSE PAS par nos chemins — disparaîtrait sans qu'aucun test ne
    rougisse.

    On vérifie aussi les deux valeurs qui doivent rester en place : exiger une revue bloquerait
    `merge-mr` sur chaque PR (aucun relecteur n'est posé d'office, #196), et `strict: true`
    imposerait de ramener `main` dans chaque PR avant de merger (docs/10 §8.3).
    """
    script = (RACINE / "scripts" / "github" / "protect-main.sh").read_text(encoding="utf-8")
    corps = re.search(r"corps_json\(\).*?\nEOF", script, re.DOTALL)
    assert corps, "le corps du PUT a changé de forme — revoir ce test avec"
    texte = corps.group(0)

    assert '"enforce_admins": true' in texte, (
        "enforce_admins doit rester à true : à false, la protection ne couvre pas le compte "
        "propriétaire, seul compte qui merge sur ce dépôt (#734)"
    )
    assert '"required_pull_request_reviews": null' in texte, (
        "aucune revue obligatoire — ce dépôt ne pose aucun relecteur d'office (#196), et l'exiger "
        "bloquerait merge-mr sur chaque PR"
    )
    assert '"strict": false' in texte, (
        "strict doit rester false — sinon chaque PR devrait ramener main avant de merger (§8.3)"
    )
