"""Tests de la présentation de milestone enrichie — #547, lot final du parent #543.

Les trois lots précédents ont livré **sans tests**, différés ici (docs/10 §5.1). Chacun a posé un
invariant qui se perd silencieusement — c'est-à-dire sans rien casser de visible, en rendant juste
une présentation un peu moins vraie :

* **#544 — la dérivation des écrans** (`scripts/presentation/ecrans-touches.sh`). Le rattachement
  écran ↔ ticket cesse d'être un pari : il se lit dans les `Refs #<iid>` / `Closes #<iid>` que le
  hook `commit-msg` impose. Deux cas font toute la valeur du script et sont donc épinglés
  nommément : **un ticket sans surface visible rend zéro ligne** (moteur, CI, doc — l'absence est
  un résultat, pas un échec) et **la borne du motif distingue `#5` de `#54`**, sans quoi un ticket
  hériterait des écrans de son voisin décimal.

* **#545 — les démonstrations filmées** (`captures.mjs` + `parcours.mjs`). L'invariant n'est pas
  qu'un parcours réussisse, c'est qu'un parcours **en échec laisse sa ligne** au manifeste avec son
  erreur : les autres continuent, les captures ne s'en aperçoivent pas, et le code de retour ne
  dépend que des captures. Un parcours qui disparaîtrait du manifeste rendrait « jamais tenté » et
  « tenté, échoué » indiscernables.

* **#546 — le rendu** (`build.py`). La promesse du format est **un seul fichier autonome** : aucune
  ressource externe, tout en `data:`. Elle ne tient qu'avec un **plafond de taille** et son
  **repli** — un clip écarté garde sa place et dit pourquoi, plutôt que de laisser un trou ou de
  gonfler le fichier au-delà de ce qui se partage.

--- Ni navigateur, ni stack -------------------------------------------------------------------

Aucun test ne démarre l'API, ne construit l'UI ni n'ouvre Edge.

* la **dérivation** se joue sur un **dépôt jetable** dans `tmp_path`, où le script est recopié —
  il lit `git -C <sa propre racine>`, donc c'est sa copie qui décide du dépôt observé ;
* le **tournage** se joue contre un **faux `playwright-core`** écrit par le test et désigné par
  `MAESTRO_PLAYWRIGHT_HOME` — la porte de secours que `chargerPlaywright()` ouvre déjà pour
  `captures.sh`. Même esprit que le `gh` factice de `tests/harnais_forge.py` : ce sont les
  DÉCISIONS du script qui sont testées, jamais Playwright ;
* le **rendu** se joue sur des fixtures — un PNG et un webm de quelques octets, un JSON écrit sur
  place ;
* l'**accord des origines** entre l'UI des captures et leur API (#1233) se juge sur l'app montée en
  mémoire (`TestClient`), avec l'environnement que `captures.sh` donne à son API — aucun port
  ouvert.

⚠ **Où chaque section peut répondre.** Les tests de la dérivation et du rendu tournent partout.
Ceux du tournage exigent `node` : présent sur les postes (`.tools/node`, §Environnement Node) et
sur le runner `ubuntu-latest` du job `pytest`, **absent de l'image `python:3.11`** du filet CI
local (#372) — ils y seront donc SAUTÉS, et le verdict qui les joue est celui de la pipeline. Un
saut qui passerait inaperçu **en CI** est exactement le défaut de #333 : c'est pourquoi
`test_node_ne_manque_pas_en_ci` en fait une erreur franche plutôt qu'un `s` de plus dans le compte
rendu.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.config import load_settings
from maestro.controltower.acces import politique_depuis
from maestro.controltower.app import create_app

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")
NODE = shutil.which("node")

COMMANDE = RACINE / ".claude" / "commands" / "milestone-presentation.md"
ECRANS_TOUCHES = RACINE / "scripts" / "presentation" / "ecrans-touches.sh"
CAPTURES_MJS = RACINE / "scripts" / "presentation" / "captures.mjs"
PARCOURS_MJS = RACINE / "scripts" / "presentation" / "parcours.mjs"
BUILD_PY = RACINE / "scripts" / "presentation" / "build.py"

#: Les mêmes jetons que `tests/conftest.py` : leur présence dit « personne n'est là pour lire un
#: `s` dans le compte rendu ».
_CLES_CI = ("CI", "GITLAB_CI", "GITHUB_ACTIONS")


# --- Fixtures d'octets ----------------------------------------------------------------------------
# Ni l'un ni l'autre n'a besoin d'être décodable : `build.py` lit des OCTETS et les encode en
# base64. Des en-têtes plausibles suivis de remplissage disent ce qu'ils sont — un faux fichier —
# sans faire croire à une image ou à une vidéo réelle.

EN_TETE_PNG = b"\x89PNG\r\n\x1a\n"
EN_TETE_WEBM = b"\x1a\x45\xdf\xa3"


def octets_png(taille: int = 128) -> bytes:
    return EN_TETE_PNG + b"\x00" * max(0, taille - len(EN_TETE_PNG))


def octets_webm(taille: int) -> bytes:
    return EN_TETE_WEBM + b"\x00" * max(0, taille - len(EN_TETE_WEBM))


# ==================================================================================================
# 1. La dérivation des écrans — `scripts/presentation/ecrans-touches.sh` (#544)
# ==================================================================================================

besoin_de_git = pytest.mark.skipif(
    BASH is None or GIT is None, reason="bash et git sont requis pour le dépôt jetable"
)


class DepotEcrans:
    """Un dépôt jetable où `ecrans-touches.sh` est recopié, et qu'il lira donc lui-même.

    Le script résout sa racine par `$(dirname $BASH_SOURCE)/../..` puis fait tous ses `git -C` sur
    elle : le recopier sous `scripts/presentation/` du dépôt d'essai suffit à lui faire observer ce
    dépôt-là. Rien à injecter, aucune variable à poser — c'est la même mécanique qu'en production.
    """

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        racine.mkdir(parents=True)
        self._git("init", "-b", "main")
        # Identité LOCALE : l'image du job n'en pose aucune globalement, et c'est délibéré (#333).
        self._git("config", "user.email", "essai@maestro.test")
        self._git("config", "user.name", "Essai Maestro")
        cible = racine / "scripts" / "presentation"
        cible.mkdir(parents=True)
        shutil.copy2(ECRANS_TOUCHES, cible / ECRANS_TOUCHES.name)
        # Commité à part, et surtout PAS par `commit()`, qui réécrit le contenu de ce qu'il ajoute
        # — il l'aurait remplacé par son bouchon, shebang compris.
        self._git("add", "--", "scripts/presentation/ecrans-touches.sh")
        self._git("commit", "-m", "chore: squelette\n\nRefs #1")

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [GIT, *args],
            cwd=self.racine,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def commit(self, message: str, chemins: list[str]) -> None:
        """Crée (ou touche) les chemins donnés et les commite sous ce message."""
        for chemin in chemins:
            fichier = self.racine / chemin
            fichier.parent.mkdir(parents=True, exist_ok=True)
            fichier.write_text(f"// {chemin}\n", encoding="utf-8")
            self._git("add", "--", chemin)
        self._git("commit", "-m", message)

    def ecrit(self, chemin: str, *, suivi: bool = False) -> None:
        """Pose un fichier SANS le commiter — le régime d'une session qui vient d'écrire.

        `suivi=True` le commite d'abord puis le modifie : c'est l'autre moitié de ce que
        `--travail-en-cours` doit voir (un fichier suivi modifié, et non seulement non suivi).
        """
        fichier = self.racine / chemin
        fichier.parent.mkdir(parents=True, exist_ok=True)
        if suivi:
            fichier.write_text(f"// {chemin}\n", encoding="utf-8")
            self._git("add", "--", chemin)
            self._git("commit", "-m", "chore: base\n\nRefs #1")
        fichier.write_text(f"// {chemin} (en cours)\n", encoding="utf-8")

    def ecrans(
        self,
        *iids: object,
        check: bool = False,
        ref: str | None = None,
        travail: bool = False,
        chemins: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = ["scripts/presentation/ecrans-touches.sh"]
        if check:
            args.append("--check")
        if ref is not None:
            args += ["--ref", ref]
        if travail:
            args.append("--travail-en-cours")
        if chemins is not None:
            args.append("--chemins")
        args += [str(i) for i in iids]
        return subprocess.run(
            [BASH, *args],
            cwd=self.racine,
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=chemins,
        )


def lignes(sortie: str) -> list[tuple[str, ...]]:
    """Les lignes de données du TSV — l'en-tête `#` est ignorable, c'est son contrat."""
    return [
        tuple(ligne.split("\t"))
        for ligne in sortie.splitlines()
        if ligne and not ligne.startswith("#")
    ]


def routes(sortie: str) -> list[tuple[str, str, str]]:
    """(iid, route, cle) — sans la colonne `fichiers`, de longueur variable."""
    return [(ligne[0], ligne[1], ligne[2]) for ligne in lignes(sortie)]


@pytest.fixture
def depot(tmp_path: Path) -> DepotEcrans:
    return DepotEcrans(tmp_path / "depot")


@besoin_de_git
def test_l_en_tete_est_ignorable_et_le_tsv_a_quatre_colonnes(depot: DepotEcrans) -> None:
    depot.commit("feat: coûts\n\nCloses #10", ["apps/web/app/couts/page.tsx"])
    resultat = depot.ecrans(10)
    assert resultat.returncode == 0, resultat.stderr
    assert resultat.stdout.splitlines()[0] == "# iid\troute\tcle\tfichiers"
    assert [len(ligne) for ligne in lignes(resultat.stdout)] == [4]


@besoin_de_git
def test_une_route_se_derive_du_dossier_de_la_page(depot: DepotEcrans) -> None:
    depot.commit("feat: coûts\n\nCloses #10", ["apps/web/app/couts/page.tsx"])
    resultat = depot.ecrans(10)
    assert routes(resultat.stdout) == [("10", "/couts", "couts")]
    assert lignes(resultat.stdout)[0][3] == "apps/web/app/couts/page.tsx"


@besoin_de_git
def test_la_racine_de_app_separe_l_accueil_de_la_coquille(depot: DepotEcrans) -> None:
    """`app/page.tsx` EST l'accueil ; `layout.tsx` et `globals.css` sont la coquille de TOUS les
    écrans. Les rattacher à `/` reviendrait à décorer l'accueil de tout ce qui touche au thème."""
    depot.commit(
        "feat: coquille\n\nCloses #11",
        ["apps/web/app/page.tsx", "apps/web/app/layout.tsx", "apps/web/app/globals.css"],
    )
    resultat = depot.ecrans(11)
    # Les routes d'abord, l'indéterminé ensuite — c'est la clé de tri du script.
    assert routes(resultat.stdout) == [("11", "/", "accueil"), ("11", "-", "-")]
    assert "globals.css" in lignes(resultat.stdout)[1][3]


@besoin_de_git
def test_un_layout_imbrique_compte_pour_sa_route(depot: DepotEcrans) -> None:
    """L'autre moitié de la règle : imbriqué, un `layout.tsx` est bien celui de SA route."""
    depot.commit("feat: enveloppe des coûts\n\nCloses #12", ["apps/web/app/couts/layout.tsx"])
    assert routes(depot.ecrans(12).stdout) == [("12", "/couts", "couts")]


@besoin_de_git
def test_un_segment_dynamique_remonte_a_la_route_de_sa_liste(depot: DepotEcrans) -> None:
    """Une page à segment dynamique n'a pas d'entrée de menu à elle : elle vit sous sa liste, et
    c'est la seule route pour laquelle une capture existe."""
    depot.commit(
        "feat: détail d'un run\n\nCloses #13",
        ["apps/web/app/runs/[runId]/page.tsx", "apps/web/app/agents/[nom]/[onglet]/page.tsx"],
    )
    assert routes(depot.ecrans(13).stdout) == [
        ("13", "/agents", "agents"),
        ("13", "/runs", "runs"),
    ]


@besoin_de_git
def test_un_groupe_de_routes_est_transparent_dans_l_url(depot: DepotEcrans) -> None:
    """`(shell)` ne paraît pas dans l'URL : il est SAUTÉ, là où un segment dynamique TRONQUE."""
    depot.commit("feat: coûts sous shell\n\nCloses #14", ["apps/web/app/(shell)/couts/page.tsx"])
    assert routes(depot.ecrans(14).stdout) == [("14", "/couts", "couts")]


@besoin_de_git
def test_un_ticket_sans_surface_visible_ne_rend_aucune_ligne(depot: DepotEcrans) -> None:
    """Le premier des deux cas du critère. Moteur, CI, doc, outillage n'ont pas d'écran : le script
    rend ZÉRO ligne et le code de retour reste 0 — « la question a été posée », pas « échec »."""
    depot.commit(
        "feat: moteur\n\nCloses #15",
        ["maestro/engine.py", "docs/06-roadmap.md", "scripts/ci/local.sh", "tests/test_x.py"],
    )
    resultat = depot.ecrans(15)
    assert resultat.returncode == 0, resultat.stderr
    assert lignes(resultat.stdout) == []
    # …et l'en-tête est quand même là : un consommateur machine lit toujours le même format.
    assert resultat.stdout.startswith("# iid\t")


@besoin_de_git
def test_un_composant_partage_rend_une_ligne_indeterminee(depot: DepotEcrans) -> None:
    """L'autre moitié de l'arbitrage : l'absence est muette, l'INCONNU est nommé. Taire un ticket
    qui n'a touché que des composants dirait « rien changé à l'écran », ce qui est faux."""
    depot.commit(
        "feat: primitives\n\nCloses #16",
        ["apps/web/components/Bouton.tsx", "apps/web/components/Carte.tsx"],
    )
    lignes_ = lignes(depot.ecrans(16).stdout)
    assert routes(depot.ecrans(16).stdout) == [("16", "-", "-")]
    # Une seule ligne pour les deux fichiers : l'agrégation se fait par route.
    assert lignes_[0][3] == "apps/web/components/Bouton.tsx,apps/web/components/Carte.tsx"


@besoin_de_git
def test_le_catalogue_du_socle_est_ecarte_nommement(depot: DepotEcrans) -> None:
    """`apps/web/app/socle/` est une ROUTE RÉELLE qui n'est pas un écran du produit (#984) : le
    catalogue des primitives n'est pas servi en production, donc la stack de `captures.sh` — qui
    est une stack de production — ne peut pas le photographier, et l'annoncer en « écran touché »
    promettrait une capture qui n'existera jamais.

    Les deux moitiés comptent. Il ne rend **pas** de route `/socle` (sans quoi une présentation de
    jalon en attendrait une capture) et il rend quand même une ligne **indéterminée** (sans quoi
    un ticket qui n'aurait touché que lui dirait « rien changé à l'écran », ce qui est faux — c'est
    la règle du fichier : l'absence est muette, l'INCONNU est nommé).
    """
    depot.commit(
        "feat: catalogue du socle\n\nCloses #18",
        ["apps/web/app/socle/page.tsx"],
    )
    resultat = depot.ecrans(18)
    assert routes(resultat.stdout) == [("18", "-", "-")]
    assert lignes(resultat.stdout)[0][3] == "apps/web/app/socle/page.tsx"


@besoin_de_git
def test_l_ecart_du_catalogue_ne_deborde_pas_sur_ses_voisins(depot: DepotEcrans) -> None:
    """L'exclusion est NOMMÉE, jamais un motif : une route qui commence par les mêmes lettres reste
    un écran. Sans ce contrôle, un `socle-v2/` — ou n'importe quel dossier préfixé — disparaîtrait
    des présentations en silence, ce qui est exactement ce qu'un écart nommé doit éviter."""
    depot.commit(
        "feat: deux routes\n\nCloses #19",
        ["apps/web/app/socles/page.tsx", "apps/web/app/socle/page.tsx"],
    )
    assert routes(depot.ecrans(19).stdout) == [("19", "/socles", "socles"), ("19", "-", "-")]


@besoin_de_git
def test_la_plomberie_de_l_ui_est_hors_perimetre(depot: DepotEcrans) -> None:
    """`lib/` est exclu À DESSEIN, hooks compris (ils y vivent) : presque tous les tickets de la
    Control Tower y touchent, et les compter ferait rendre une ligne indéterminée à presque tous."""
    depot.commit(
        "feat: plomberie\n\nCloses #17",
        ["apps/web/lib/navigation.ts", "apps/web/lib/useControlTower.ts"],
    )
    assert lignes(depot.ecrans(17).stdout) == []


@besoin_de_git
def test_la_borne_du_motif_distingue_5_de_54(depot: DepotEcrans) -> None:
    """Le second cas du critère, et le plus coûteux s'il tombe : sans la borne « non-chiffre ou
    fin », `#5` hériterait des écrans de `#54`, `#55`, `#500`…"""
    depot.commit("feat: coûts\n\nCloses #54", ["apps/web/app/couts/page.tsx"])
    depot.commit("feat: chat\n\nRefs #5", ["apps/web/app/chat/page.tsx"])
    depot.commit("feat: validations\n\nRefs #55", ["apps/web/app/validations/page.tsx"])

    assert routes(depot.ecrans(5).stdout) == [("5", "/chat", "chat")]
    assert routes(depot.ecrans(54).stdout) == [("54", "/couts", "couts")]
    # Et la borne ne coupe pas trop court non plus : les trois répondent, chacun pour soi.
    assert routes(depot.ecrans(55).stdout) == [("55", "/validations", "validations")]


@besoin_de_git
def test_le_numero_de_pr_d_un_squash_n_est_pas_un_iid(depot: DepotEcrans) -> None:
    """GitHub suffixe le sujet d'un squash du NUMÉRO DE LA PR. Un motif sur `#<iid>` seul
    apparierait les tickets aux PR des autres — d'où le mot-clé obligatoire dans le motif."""
    depot.commit(
        "feat(web): quelque chose (#54)\n\nCloses #18",
        ["apps/web/app/agents/page.tsx"],
    )
    assert lignes(depot.ecrans(54).stdout) == []
    assert routes(depot.ecrans(18).stdout) == [("18", "/agents", "agents")]


@besoin_de_git
def test_plusieurs_commits_d_un_meme_ticket_sont_reunis(depot: DepotEcrans) -> None:
    """On ne parie pas sur l'unicité du commit : le squash en laisse un le plus souvent, la reprise
    d'un ticket peut en laisser plusieurs."""
    depot.commit("feat: coûts\n\nRefs #19", ["apps/web/app/couts/page.tsx"])
    depot.commit("feat: coûts, suite\n\nCloses #19", ["apps/web/app/chat/page.tsx"])
    assert routes(depot.ecrans(19).stdout) == [
        ("19", "/chat", "chat"),
        ("19", "/couts", "couts"),
    ]


@besoin_de_git
def test_un_iid_donne_deux_fois_ne_rend_ses_lignes_qu_une_fois(depot: DepotEcrans) -> None:
    depot.commit("feat: coûts\n\nCloses #20", ["apps/web/app/couts/page.tsx"])
    assert routes(depot.ecrans(20, "#20", 20).stdout) == [("20", "/couts", "couts")]


@besoin_de_git
def test_check_distingue_un_ticket_sans_commit_d_un_ticket_sans_ecran(
    depot: DepotEcrans,
) -> None:
    """Les deux rendent zéro ligne, et ce ne sont pas les mêmes situations : l'un n'est pas encore
    mergé, l'autre n'a pas d'écran. Seul `--check` les sépare, sur stderr."""
    depot.commit("feat: moteur\n\nCloses #21", ["maestro/engine.py"])
    resultat = depot.ecrans(21, 99, check=True)
    assert lignes(resultat.stdout) == []
    assert "#99 — aucun commit" in resultat.stderr
    assert "#21 — 1 commit(s)" in resultat.stderr
    assert "ref lue :" in resultat.stderr


@besoin_de_git
def test_un_argument_qui_n_est_pas_un_iid_est_refuse(depot: DepotEcrans) -> None:
    resultat = depot.ecrans("couts")
    assert resultat.returncode == 2
    assert "n'est pas un iid" in resultat.stderr


@besoin_de_git
def test_sans_iid_le_script_refuse_au_lieu_de_tout_balayer(depot: DepotEcrans) -> None:
    resultat = depot.ecrans()
    assert resultat.returncode == 2
    assert "au moins un iid" in resultat.stderr


@besoin_de_git
def test_le_script_n_ecrit_rien_dans_le_depot(depot: DepotEcrans) -> None:
    """Lecture seule annoncée en en-tête : pas même un fichier temporaire, et surtout aucun
    commit — une commande de supervision ne touche pas au dépôt qu'elle observe."""
    depot.commit("feat: coûts\n\nCloses #22", ["apps/web/app/couts/page.tsx"])
    avant = subprocess.run(
        [GIT, "status", "--porcelain"], cwd=depot.racine, capture_output=True, text=True
    ).stdout
    depot.ecrans(22, check=True)
    apres = subprocess.run(
        [GIT, "status", "--porcelain"], cwd=depot.racine, capture_output=True, text=True
    ).stdout
    assert avant == apres == ""


# --------------------------------------------------------------------------------------------------
# Les deux sources ajoutées par #932 — même règle de classement, d'autres fichiers à classer
# --------------------------------------------------------------------------------------------------
#
# `ecrans-touches.sh` répondait à une question posée APRÈS COUP, sur un milestone livré : les
# commits suffisaient. La relecture visuelle (#932) la pose AVANT la clôture, quand le ticket
# n'a souvent aucun commit. Ce qui se garde ici n'est donc pas une règle de plus — c'est que la
# règle reste **la même** quand la source change, et que chaque source refuse ce qu'elle ne sait
# pas attribuer.


@besoin_de_git
def test_le_travail_en_cours_ne_compte_que_si_on_le_demande(depot: DepotEcrans) -> None:
    """Le contre-exemple d'abord : sans l'option, un écran non commité n'existe pas.

    Sans cette moitié, « --travail-en-cours rend une ligne » ne prouverait rien — la ligne pourrait
    venir d'ailleurs.
    """
    depot.ecrit("apps/web/app/couts/page.tsx")
    assert routes(depot.ecrans(30).stdout) == []
    assert routes(depot.ecrans(30, travail=True).stdout) == [("30", "/couts", "couts")]


@besoin_de_git
def test_le_travail_en_cours_voit_le_suivi_modifie_autant_que_le_non_suivi(
    depot: DepotEcrans,
) -> None:
    """Deux lectures, pas une : `diff HEAD` (suivis modifiés) et `ls-files --others` (non suivis).

    Un écran retouché est le cas le plus courant d'un ticket d'interface — n'en lire qu'une des deux
    laisserait la moitié des relectures muettes.
    """
    depot.ecrit("apps/web/app/runs/page.tsx", suivi=True)
    depot.ecrit("apps/web/app/couts/page.tsx")
    assert routes(depot.ecrans(31, travail=True).stdout) == [
        ("31", "/couts", "couts"),
        ("31", "/runs", "runs"),
    ]


@besoin_de_git
def test_les_commits_et_l_arbre_se_reunissent_sous_une_seule_route(depot: DepotEcrans) -> None:
    """Un ticket qui a commité un écran puis en retouche un fichier : une route, ses deux fichiers.

    C'est l'agrégation de #544 qui répond — la source n'y change rien, et c'était l'enjeu de ne pas
    recopier la règle dans `relecture-visuelle.sh`.
    """
    depot.commit("feat: runs\n\nRefs #32", ["apps/web/app/runs/page.tsx"])
    depot.ecrit("apps/web/app/runs/Filtres.tsx")
    vues = lignes(depot.ecrans(32, ref="HEAD", travail=True).stdout)
    assert [(ligne[0], ligne[1]) for ligne in vues] == [("32", "/runs")]
    assert vues[0][3] == "apps/web/app/runs/Filtres.tsx,apps/web/app/runs/page.tsx"


@besoin_de_git
def test_un_fichier_ignore_n_est_pas_du_travail_en_cours(depot: DepotEcrans) -> None:
    """`--exclude-standard` : ce que le `.gitignore` masque n'entre jamais dans un commit, donc
    n'est pas du travail à relire. Le dépôt en pose sous `apps/web/` (build, captures)."""
    (depot.racine / ".gitignore").write_text("apps/web/app/brouillon/\n", encoding="utf-8")
    depot._git("add", "--", ".gitignore")
    depot._git("commit", "-m", "chore: motif\n\nRefs #1")
    depot.ecrit("apps/web/app/brouillon/page.tsx")
    depot.ecrit("apps/web/app/couts/page.tsx")
    assert routes(depot.ecrans(33, travail=True).stdout) == [("33", "/couts", "couts")]


@besoin_de_git
def test_le_travail_en_cours_refuse_deux_tickets(depot: DepotEcrans) -> None:
    """L'arbre appartient à la BRANCHE : l'étaler sur N tickets prêterait à chacun les fichiers des
    autres, et silencieusement — les lignes se ressembleraient."""
    resultat = depot.ecrans(34, 35, travail=True)
    assert resultat.returncode == 2
    assert "--travail-en-cours attend un seul iid" in resultat.stderr


@besoin_de_git
def test_chemins_ne_lit_ni_les_commits_ni_l_arbre(depot: DepotEcrans) -> None:
    """Le contre-exemple est dans le décor : le ticket a un écran commité ET un écran non commité,
    et aucun des deux ne sort. `--chemins` ne rend QUE la règle, sur la liste qu'on lui donne."""
    depot.commit("feat: coûts\n\nCloses #36", ["apps/web/app/couts/page.tsx"])
    depot.ecrit("apps/web/app/runs/page.tsx")
    sortie = depot.ecrans(36, chemins="apps/web/app/agents/page.tsx\n").stdout
    assert routes(sortie) == [("36", "/agents", "agents")]


@besoin_de_git
def test_chemins_classe_avec_la_meme_regle_que_les_commits(depot: DepotEcrans) -> None:
    """Un composant partagé rend « - » ici comme ailleurs : c'est ce qui permet à
    `relecture-visuelle.sh` de remonter vers les écrans qui l'affichent sans jamais redéfinir
    « indéterminé »."""
    sortie = depot.ecrans(
        37,
        chemins="apps/web/components/Conversation.tsx\napps/web/app/chat/page.tsx\n",
    ).stdout
    assert routes(sortie) == [("37", "/chat", "chat"), ("37", "-", "-")]


@besoin_de_git
def test_chemins_dedoublonne_ce_qu_on_lui_donne(depot: DepotEcrans) -> None:
    """`sort -u` en entrée, comme pour les commits : deux importateurs d'un même composant mènent au
    même écran, et le plan ne doit pas le nommer deux fois."""
    sortie = depot.ecrans(
        38, chemins="apps/web/app/runs/page.tsx\napps/web/app/runs/page.tsx\n"
    ).stdout
    assert routes(sortie) == [("38", "/runs", "runs")]


@besoin_de_git
def test_chemins_refuse_deux_tickets(depot: DepotEcrans) -> None:
    """Même garde que `--travail-en-cours`, pour une raison qui lui est propre et que l'aide
    annonçait déjà : stdin n'est lisible qu'une fois, donc le second iid ne recevrait rien. En
    ignorer un en silence était le défaut symétrique de celui que l'autre garde empêche."""
    resultat = depot.ecrans(39, 40, chemins="apps/web/app/runs/page.tsx\n")
    assert resultat.returncode == 2
    assert "--chemins attend un seul iid" in resultat.stderr


# ==================================================================================================
# 2. Le tournage — `captures.mjs` contre un faux playwright-core (#545)
# ==================================================================================================

besoin_de_node = pytest.mark.skipif(NODE is None, reason="node introuvable")

#: Le stub. Il n'imite pas Playwright — il en rend juste assez pour que les DÉCISIONS de
#: `captures.mjs` se jouent : un contexte sait s'il enregistre une vidéo, une page sait écrire un
#: fichier, et deux commutateurs fabriquent les deux régimes d'échec d'un parcours.
#:
#:   FAUX_ECHEC_CONTEXTE_VIDEO=<n>  le n-ième contexte vidéo refuse de s'ouvrir → échec DUR :
#:                                  aucun clip, la ligne doit rester au manifeste avec son erreur.
#:   FAUX_PAGE_NON_PRETE_VIDEO=<n>  la page du n-ième clip n'atteint jamais son signal de page
#:                                  prête → depuis #830 ce n'est plus qu'un AVERTISSEMENT : les
#:                                  gestes sont joués quand même, et le clip est complet.
#:   FAUX_GESTES_OK_VIDEO=<n>:<k>   les gestes du n-ième clip cessent de répondre après le k-ième
#:                                  (#830) — `1:0` fabrique le clip MUET (aucun geste, écarté),
#:                                  `1:2` le clip ÉCOURTÉ en cours de route (conservé).
#:   FAUX_ECHEC_CAPTURES=1          toutes les captures échouent → c'est le seul cas qui doit
#:                                  changer le code de retour.
#:   FAUX_ECRITURE_VIDEO=<n>        le premier clic du n-ième clip fait partir, par les routes du
#:                                  contexte, une LECTURE puis une ÉCRITURE vers l'API (#1166) :
#:                                  la garde doit laisser l'une et refuser l'autre.
#:   FAUX_TRACE=<fichier>           le stub y consigne, une ligne JSON par fait, ce que le script
#:                                  lui a demandé : les arguments des scripts d'initialisation
#:                                  (`init`) et le sort de chaque requête routée (`continue`,
#:                                  `abort`) — ce qu'un test ne peut pas lire dans le manifeste.
FAUX_PLAYWRIGHT = """\
"use strict";
// Faux playwright-core — écrit par tests/test_presentation.py (#547). Aucun navigateur.
const { appendFileSync, mkdirSync, writeFileSync } = require("node:fs");
const { dirname } = require("node:path");

const echecContexte = Number(process.env.FAUX_ECHEC_CONTEXTE_VIDEO || 0);
const pageNonPrete = Number(process.env.FAUX_PAGE_NON_PRETE_VIDEO || 0);
const echecCaptures = process.env.FAUX_ECHEC_CAPTURES === "1";
const ecritureVideo = Number(process.env.FAUX_ECRITURE_VIDEO || 0);
const trace = process.env.FAUX_TRACE || "";
// « <rang>:<k> » — NaN quand la variable est absente, ce qui ne matche aucun rang.
const gestesRegle = String(process.env.FAUX_GESTES_OK_VIDEO || "").split(":").map(Number);
const [gestesRang, gestesMax] = gestesRegle;
let contextesVideo = 0;

function ecrire(chemin, contenu) {
  mkdirSync(dirname(chemin), { recursive: true });
  writeFileSync(chemin, contenu);
}

function tracer(fait) {
  if (trace) appendFileSync(trace, JSON.stringify(fait) + "\\n");
}

// Une requête qui traverse les routes du contexte, comme le navigateur les ferait
// traverser : le premier gestionnaire enregistré la reçoit.
async function router(routes, methode, url) {
  const route = {
    request: () => ({ method: () => methode, url: () => url }),
    async continue() { tracer({ continue: `${methode} ${url}` }); },
    async abort(raison) { tracer({ abort: `${methode} ${url}`, raison }); },
  };
  for (const [, gestionnaire] of routes) {
    await gestionnaire(route);
    return;
  }
  tracer({ sans_route: `${methode} ${url}` });
}

function faussePage(rangVideo, routes) {
  // Ce que le stub compte est ce que `jouerGeste` APPELLE : `waitFor` pour un
  // « attendre », `click` pour un « cliquer », `evaluate` pour un « defiler ».
  let gestes = 0;
  let ecrit = false;
  const geste = () => {
    if (!rangVideo || rangVideo !== gestesRang) return;
    // Le message imite celui de Playwright : une première ligne, puis son journal
    // d'appels en couleur — que le manifeste ne doit pas recopier (#1166).
    if (gestes >= gestesMax) {
      throw new Error("geste impossible (faux)\\nCall log:\\n" +
        "\\u001b[2m  - attente (faux)\\u001b[22m");
    }
    gestes += 1;
  };
  const locator = {
    first: () => locator,
    filter: () => locator,
    async count() { return 1; },
    async click() {
      geste();
      if (rangVideo && rangVideo === ecritureVideo && !ecrit) {
        ecrit = true;
        await router(routes, "GET", "http://127.0.0.1:9/api/validations?projet=p");
        await router(routes, "POST", "http://127.0.0.1:9/api/validations/v1/decision");
      }
    },
    async waitFor() { geste(); },
  };
  return {
    async goto() {
      if (!rangVideo && echecCaptures) throw new Error("route injoignable (faux)");
    },
    async waitForFunction() {
      if (rangVideo && rangVideo === pageNonPrete) throw new Error("marqueur absent (faux)");
    },
    async waitForTimeout() {},
    async evaluate() { geste(); },
    async screenshot({ path }) { ecrire(path, "capture-factice"); },
    getByText: () => locator,
    locator: () => locator,
    video() {
      if (!rangVideo) return null;
      return {
        async saveAs(destination) { ecrire(destination, "clip-factice"); },
        async delete() {},
      };
    },
    async close() {},
  };
}

module.exports = {
  chromium: {
    async launch() {
      return {
        async newContext(options) {
          let rangVideo = 0;
          if (options && options.recordVideo) {
            contextesVideo += 1;
            rangVideo = contextesVideo;
            if (echecContexte && rangVideo === echecContexte) {
              throw new Error("enregistrement impossible (faux)");
            }
          }
          const routes = [];
          return {
            async addInitScript(_fonction, args) { tracer({ init: args, video: rangVideo }); },
            async route(motif, gestionnaire) { routes.push([motif, gestionnaire]); },
            async newPage() { return faussePage(rangVideo, routes); },
            async close() {},
          };
        },
        async close() {},
      };
    },
  },
};
"""


def cles_de_parcours() -> list[str]:
    """Les clés déclarées dans `parcours.mjs` — lues, jamais recopiées.

    Le test épingle ainsi « TOUS les parcours laissent une ligne », y compris ceux qu'on ajoutera :
    une liste en dur ici vieillirait au premier parcours suivant, et le test cesserait de garder la
    moitié du critère qui compte (la LIGNE, pas le clip).
    """
    texte = PARCOURS_MJS.read_text(encoding="utf-8")
    return re.findall(r'^\s+cle:\s*"([^"]+)"', texte, re.MULTILINE)


@pytest.fixture
def maison_playwright(tmp_path: Path) -> Path:
    """Le dossier que `MAESTRO_PLAYWRIGHT_HOME` désigne — la porte de secours de
    `chargerPlaywright()`, celle-là même par laquelle `captures.sh` passe en production."""
    maison = tmp_path / "faux-playwright"
    module = maison / "node_modules" / "playwright-core"
    module.mkdir(parents=True)
    (maison / "package.json").write_text('{"name":"maison","version":"1.0.0"}\n', encoding="utf-8")
    (module / "package.json").write_text(
        '{"name":"playwright-core","version":"0.0.0-faux","main":"index.js"}\n', encoding="utf-8"
    )
    (module / "index.js").write_text(FAUX_PLAYWRIGHT, encoding="utf-8")
    return maison


def tourner(
    sortie: Path, maison: Path, *options: str, **commutateurs: str
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Joue `captures.mjs` contre le stub et rend (processus, manifeste).

    L'API vise le port `discard` sauf si le test en désigne une : sans ça, la série
    interrogerait la vraie Control Tower du poste (8000), et son verdict dépendrait
    de ce qui y tourne.
    """
    environnement = dict(os.environ)
    environnement["MAESTRO_PLAYWRIGHT_HOME"] = str(maison)
    environnement.update(commutateurs)
    appel = [NODE, str(CAPTURES_MJS), "--sortie", str(sortie), "--base", "http://127.0.0.1:9"]
    if "--api" not in options:
        appel += ["--api", "http://127.0.0.1:9"]
    processus = subprocess.run(
        [*appel, *options],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environnement,
        cwd=RACINE,
    )
    manifeste_fichier = sortie / "captures.json"
    manifeste = (
        json.loads(manifeste_fichier.read_text(encoding="utf-8"))
        if manifeste_fichier.exists()
        else {}
    )
    return processus, manifeste


def test_node_ne_manque_pas_en_ci() -> None:
    """La leçon de #333, appliquée à `node` : en CI, un saut n'est pas une réponse.

    Sur un poste, `skipif` dit « cette machine ne peut pas répondre » et c'est vrai. En intégration
    continue, il dirait « tout va bien » avec les mots de « rien n'a été vérifié » — et le tournage
    de #545 n'aurait alors JAMAIS été joué nulle part. Le job `pytest` tourne sur le runner hébergé
    `ubuntu-latest`, qui livre node ; le seul geste qui le reperdrait est un `container:`.
    """
    if NODE is not None:
        return
    if not any(os.environ.get(cle) for cle in _CLES_CI):
        pytest.skip("poste sans node : le skipif de chaque test reste la bonne réponse")
    pytest.fail(
        "node est introuvable alors que la suite tourne en intégration continue : les tests du "
        "tournage (#545) seraient SAUTÉS en silence et la pipeline resterait verte (#333). "
        "Rendre node disponible dans le job `pytest` de .github/workflows/ci.yml."
    )


@besoin_de_node
def test_le_manifeste_porte_les_deux_listes(tmp_path: Path, maison_playwright: Path) -> None:
    """Le contrat que `build.py` consomme : `pages` ET `videos`, dans un seul fichier."""
    sortie = tmp_path / "captures"
    processus, manifeste = tourner(sortie, maison_playwright)
    assert processus.returncode == 0, processus.stderr

    assert set(manifeste) >= {"base", "genere", "viewport", "pages", "videos"}
    assert manifeste["pages"], "aucune page capturée — le menu n'a pas été lu ?"
    assert all(page["fichier"] for page in manifeste["pages"])
    assert [clip["cle"] for clip in manifeste["videos"]] == cles_de_parcours()
    assert all(clip["fichier"] and clip["complet"] for clip in manifeste["videos"])
    # Le compte de gestes (#830) : il porte la distinction « écourté » / « muet », et un clip
    # nominal les a tous joués. `> 0` n'est pas décoratif — sans lui, un parcours qui aurait perdu
    # ses gestes rendrait « 0 == 0 » et passerait pour complet.
    assert all(
        clip["gestes_joues"] == clip["gestes"] > 0 for clip in manifeste["videos"]
    ), "un parcours nominal n'a pas joué tous ses gestes"
    # Le dossier de travail de Playwright ne survit pas à la série.
    assert not (sortie / "videos-brutes").exists()


@besoin_de_node
def test_un_parcours_en_echec_laisse_sa_ligne_au_manifeste(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """Le critère. Un parcours qui échoue DUR — rien à sauvegarder, pas de clip du tout — laisse
    quand même sa ligne, avec `fichier: null` et son erreur.

    C'est ce qui distingue « jamais tenté » de « tenté, échoué » : une ligne absente ne dit rien,
    et la présentation n'aurait aucun moyen de le mentionner.
    """
    sortie = tmp_path / "captures"
    processus, manifeste = tourner(
        sortie, maison_playwright, FAUX_ECHEC_CONTEXTE_VIDEO="2"
    )

    clips = manifeste["videos"]
    assert [c["cle"] for c in clips] == cles_de_parcours(), "un parcours a disparu du manifeste"

    echoue = clips[1]
    assert echoue["fichier"] is None
    assert echoue["octets"] is None
    assert echoue["complet"] is False
    assert "enregistrement impossible" in echoue["erreur"]
    assert not (sortie / f"{echoue['cle']}.webm").exists()
    # …et il ne se confond pas avec le clip MUET de #830, qui lui aussi sort sans fichier : ici
    # rien n'a pu COMMENCER, d'où `null` et non `0`. Les deux ne se soignent pas pareil, et le
    # bilan de fin ne compte que les seconds.
    assert echoue["gestes_joues"] is None
    assert "aucun geste" not in processus.stderr

    # Les autres continuent : c'est la seconde moitié de la règle.
    autres = clips[:1] + clips[2:]
    assert autres and all(c["fichier"] and c["erreur"] is None for c in autres)

    # …et les captures ne s'en aperçoivent pas. Le code de retour ne dépend QUE d'elles.
    assert processus.returncode == 0, processus.stderr
    assert all(page["fichier"] for page in manifeste["pages"])


@besoin_de_node
def test_une_page_non_prete_ne_coupe_plus_les_gestes(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """Le renversement de #830, et la moitié qui a tué le tournage de #545.

    La page qui n'atteint pas son signal faisait deux choses, toutes deux fausses : elle mangeait
    le budget du clip en attente, puis son échec ABANDONNAIT les gestes — d'où cinq clips immobiles
    conservés, `complet: false`, indiscernables d'un clip écourté à mi-parcours. L'attente n'est
    plus qu'un avertissement : les gestes sont joués, et s'ils jouent, le clip est complet — le
    signal seul avait tort.
    """
    sortie = tmp_path / "captures"
    processus, manifeste = tourner(sortie, maison_playwright, FAUX_PAGE_NON_PRETE_VIDEO="1")

    malgre_tout = manifeste["videos"][0]
    assert malgre_tout["fichier"], "le clip d'une page non prête n'a pas été tourné"
    assert (sortie / malgre_tout["fichier"]).exists()
    assert malgre_tout["gestes_joues"] == malgre_tout["gestes"] > 0, (
        "les gestes ont encore été abandonnés parce que la page n'était pas prête"
    )
    assert malgre_tout["complet"] is True
    assert malgre_tout["erreur"] is None
    # L'avertissement n'est pas perdu pour autant : il se lit, il ne décide plus.
    assert "page non prête" in processus.stderr
    assert all(c["complet"] for c in manifeste["videos"][1:])


@besoin_de_node
def test_un_clip_sans_aucun_geste_est_ecarte_et_dit_pourquoi(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """Le critère : un clip qui n'a joué AUCUN geste ne démontre rien, donc il n'est pas proposé.

    Ce n'est pas de la propreté : c'est ce qui rend la panne visible. Un enregistrement immobile
    conservé sous `complet: false` se sélectionne comme les autres — et c'est ainsi que deux
    présentations de jalon sont parties sans une démonstration valide, sans une ligne rouge.
    La LIGNE reste (règle de #545) : « jamais tenté » et « tenté, muet » ne se confondent pas.
    """
    sortie = tmp_path / "captures"
    processus, manifeste = tourner(sortie, maison_playwright, FAUX_GESTES_OK_VIDEO="1:0")

    assert [c["cle"] for c in manifeste["videos"]] == cles_de_parcours(), "un parcours a disparu"

    muet = manifeste["videos"][0]
    assert muet["gestes_joues"] == 0 and muet["gestes"] > 0
    assert muet["fichier"] is None, "un clip immobile est encore proposé à la sélection"
    assert muet["octets"] is None
    assert muet["complet"] is False
    assert muet["erreur"].startswith("aucun geste joué")
    # La cause dit ce que l'écran n'a pas montré (#1166), sur une ligne : le journal d'appels de
    # Playwright et ses codes de couleur ne voyagent pas jusqu'au manifeste.
    assert "n'est pas à l'écran" in muet["erreur"] or "rien de cliquable" in muet["erreur"]
    assert "geste impossible (faux)" in muet["erreur"]
    assert "\n" not in muet["erreur"] and "\x1b" not in muet["erreur"]
    assert "Call log" not in muet["erreur"]
    # Écarté veut dire écarté : rien n'est laissé sur le disque non plus.
    assert not (sortie / f"{muet['cle']}.webm").exists()
    # …et il est NOMMÉ dans le compte rendu, séparément du « n/N filmé(s) » : un tournage muet ne
    # se lit pas comme une panne de tournage, sa cause est ailleurs.
    assert "aucun geste" in processus.stderr and muet["cle"] in processus.stderr

    # Les autres continuent, et les captures ne s'en aperçoivent pas (règle de #545).
    assert all(c["fichier"] and c["complet"] for c in manifeste["videos"][1:])
    assert processus.returncode == 0, processus.stderr


@besoin_de_node
def test_un_clip_ecourte_en_cours_de_route_reste_conserve(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """L'autre moitié du même critère, et elle seule prouve que l'écart n'est pas devenu aveugle :
    un parcours qui a joué DEUX gestes sur six a montré quelque chose — il garde son clip."""
    sortie = tmp_path / "captures"
    _, manifeste = tourner(sortie, maison_playwright, FAUX_GESTES_OK_VIDEO="1:2")

    ecourte = manifeste["videos"][0]
    assert ecourte["gestes_joues"] == 2 and ecourte["gestes"] > 2
    assert ecourte["fichier"], "un clip écourté se conserve — il montre ce qu'il a pu montrer"
    assert (sortie / ecourte["fichier"]).exists()
    assert ecourte["complet"] is False
    assert ecourte["erreur"].startswith("geste 3/")


@besoin_de_node
def test_sans_videos_le_manifeste_garde_ses_pages_et_perd_ses_clips(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """`--sans-videos` rend l'appel d'avant #545 : mêmes captures, aucun clip."""
    sortie = tmp_path / "captures"
    processus, manifeste = tourner(sortie, maison_playwright, "--sans-videos")
    assert processus.returncode == 0, processus.stderr
    assert manifeste["videos"] == []
    assert manifeste["pages"] and all(p["fichier"] for p in manifeste["pages"])
    assert "désactivés" in processus.stderr


@besoin_de_node
def test_seules_les_captures_decident_du_code_de_retour(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """Le pendant du test du parcours en échec : zéro capture EST un échec, parce que l'appelant
    doit pouvoir enchaîner sur le repli « présentation sans visuels »."""
    sortie = tmp_path / "captures"
    processus, manifeste = tourner(sortie, maison_playwright, FAUX_ECHEC_CAPTURES="1")
    assert processus.returncode == 1
    assert all(page["fichier"] is None and page["erreur"] for page in manifeste["pages"])


# --- La vraie stack, plus la démo (#1166) ---------------------------------------------------------
#
# La série tourne désormais sur la Control Tower réelle, rouverte sur l'état qu'un passage du banc
# a laissé. Trois décisions du script en découlent, et c'est elles qu'on épingle ici, toujours
# contre le stub : le projet ouvert est CHOISI parmi ceux que l'API déclare (plus un `prj-demo`
# écrit d'avance) ; la provenance voyage jusqu'au manifeste ; et une série sur le réel MONTRE sans
# rien exercer — une écriture vers l'API est refusée à la source et nommée.


def lire_trace(chemin: Path) -> list[dict[str, Any]]:
    """Les faits que le stub a consignés (`FAUX_TRACE`), dans l'ordre."""
    if not chemin.exists():
        return []
    return [json.loads(ligne) for ligne in chemin.read_text(encoding="utf-8").splitlines()]


def projets_poses(trace: list[dict[str, Any]]) -> set[Any]:
    """Les projets actifs que les scripts d'initialisation ont posés (4e argument, `projetId`)."""
    return {fait["init"][3] for fait in trace if "init" in fait}


class FausseApi:
    """Une API qui ne sait rendre que ce que la série lui demande avant de commencer."""

    def __init__(self, projets: list[dict[str, Any]], espace: str = "copie.banc") -> None:
        import http.server
        import threading

        corps = {
            "/api/sante": {"statut": "ok", "espace": espace},
            "/api/projets": projets,
        }

        class Gestionnaire(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — nom imposé par http.server
                reponse = corps.get(self.path.split("?", 1)[0])
                if reponse is None:
                    self.send_error(404)
                    return
                octets = json.dumps(reponse).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(octets)))
                self.end_headers()
                self.wfile.write(octets)

            def log_message(self, *_args: Any) -> None:
                return

        self.serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gestionnaire)
        self.url = f"http://127.0.0.1:{self.serveur.server_address[1]}"
        self._fil = threading.Thread(target=self.serveur.serve_forever, daemon=True)

    def __enter__(self) -> FausseApi:
        self._fil.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.serveur.shutdown()
        self.serveur.server_close()


def projet(identifiant: str, nom: str, modifie_le: str) -> dict[str, Any]:
    return {"id": identifiant, "nom": nom, "cree_le": "2026-09-22T08:00:00+00:00",
            "modifie_le": modifie_le}


@besoin_de_node
def test_le_projet_ouvert_est_celui_que_l_api_declare_et_qui_a_bouge_en_dernier(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """Le critère « ne déclarent plus `prj-demo` » : le projet vient de l'API réelle, jamais d'un
    identifiant écrit d'avance — et il est posé dans CHAQUE contexte, captures comme clips."""
    trace = tmp_path / "trace.jsonl"
    declares = [
        projet("prj-aaaa0001", "banc-s1-vider", "2026-09-22T09:00:00+00:00"),
        projet("prj-bbbb0002", "banc-s4-pourquoi", "2026-09-22T09:40:00+00:00"),
        projet("prj-cccc0003", "banc-s2-appli", "2026-09-22T09:20:00+00:00"),
    ]
    with FausseApi(declares) as api:
        processus, manifeste = tourner(
            tmp_path / "captures", maison_playwright, "--api", api.url, FAUX_TRACE=str(trace)
        )

    assert processus.returncode == 0, processus.stderr
    source = manifeste["source"]
    assert source["stack"] == "reelle"
    assert source["espace"] == "copie.banc"
    assert source["projet"] == {"id": "prj-bbbb0002", "nom": "banc-s4-pourquoi"}
    assert source["projets"] == 3
    assert source["alerte"] is None
    assert projets_poses(lire_trace(trace)) == {"prj-bbbb0002"}, (
        "un contexte s'est ouvert sur un autre projet que celui que le manifeste annonce"
    )


@besoin_de_node
def test_un_projet_demande_l_emporte_et_un_inconnu_est_nomme(
    tmp_path: Path, maison_playwright: Path
) -> None:
    declares = [
        projet("prj-aaaa0001", "banc-s1-vider", "2026-09-22T09:00:00+00:00"),
        projet("prj-bbbb0002", "banc-s4-pourquoi", "2026-09-22T09:40:00+00:00"),
    ]
    with FausseApi(declares) as api:
        _p, demande = tourner(
            tmp_path / "a", maison_playwright, "--api", api.url, "--projet", "prj-aaaa0001"
        )
        _p, inconnu = tourner(
            tmp_path / "b", maison_playwright, "--api", api.url, "--projet", "prj-demo"
        )

    assert demande["source"]["projet"] == {"id": "prj-aaaa0001", "nom": "banc-s1-vider"}
    assert inconnu["source"]["projet"] is None
    assert "prj-demo" in inconnu["source"]["alerte"], "l'identifiant refusé n'est pas nommé"


@besoin_de_node
def test_sans_api_la_serie_part_sans_projet_et_le_dit(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """Une API muette ne se remplace par rien de fabriqué : aucun projet n'est posé, la porte
    d'entrée rend ce qu'elle rend, et le manifeste nomme la cause. Les captures, elles, restent le
    seul verdict du code de retour."""
    trace = tmp_path / "trace.jsonl"
    processus, manifeste = tourner(tmp_path / "captures", maison_playwright, FAUX_TRACE=str(trace))

    assert processus.returncode == 0, processus.stderr
    assert manifeste["source"]["projet"] is None
    assert "API injoignable" in manifeste["source"]["alerte"]
    assert projets_poses(lire_trace(trace)) == {None}


@besoin_de_node
def test_l_etat_rouvert_voyage_jusqu_au_manifeste(tmp_path: Path, maison_playwright: Path) -> None:
    """Ce que `etat.py --decrire` a écrit est ce que la présentation dira de ses pièces."""
    decrit = {"passage": "20260922-120842", "sauve_le": "2026-09-22T09:10:00+00:00",
              "age_s": 60, "scenarios": [{"id": "S4", "verdict": "vert"}],
              "evenements": 13, "projets": 1}
    fichier = tmp_path / "etat.json"
    fichier.write_text(json.dumps(decrit), encoding="utf-8")

    _p, avec = tourner(tmp_path / "a", maison_playwright, "--etat", str(fichier))
    _p, sans = tourner(tmp_path / "b", maison_playwright)

    assert avec["source"]["etat"] == decrit
    assert sans["source"]["etat"] is None, "sans description, rien n'est deviné"


@besoin_de_node
def test_une_ecriture_tentee_pendant_le_tournage_est_refusee_et_nommee(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """Le garde-fou : sur le réel, un clic qui accorderait une validation ou lancerait un run
    dépenserait du vrai modèle et changerait l'état photographié. La lecture passe, l'écriture est
    refusée À LA SOURCE, et la ligne du clip la nomme."""
    trace = tmp_path / "trace.jsonl"
    # Le premier parcours qui clique : c'est lui que le stub fait écrire.
    rangs = [i for i, p in enumerate(gestes_de_parcours(), 1) if "cliquer" in p]
    assert rangs, "aucun parcours ne clique : le test ne poserait aucune question"
    _p, manifeste = tourner(
        tmp_path / "captures", maison_playwright,
        FAUX_ECRITURE_VIDEO=str(rangs[0]), FAUX_TRACE=str(trace),
    )

    faits = lire_trace(trace)
    assert {"continue": "GET http://127.0.0.1:9/api/validations?projet=p"} in faits
    refus = [fait for fait in faits if "abort" in fait]
    assert [fait["abort"] for fait in refus] == [
        "POST http://127.0.0.1:9/api/validations/v1/decision"
    ]
    clip = manifeste["videos"][rangs[0] - 1]
    assert clip["ecritures_refusees"] == ["POST /api/validations/v1/decision"]
    autres = [v for v in manifeste["videos"] if v is not clip]
    assert all(v["ecritures_refusees"] == [] for v in autres)
    assert manifeste["source"]["ecritures_refusees"] == [], "les captures n'ont rien tenté"


def gestes_de_parcours() -> list[set[str]]:
    """Les verbes de chaque parcours, dans l'ordre de `parcours.mjs` — lus, jamais recopiés."""
    texte = PARCOURS_MJS.read_text(encoding="utf-8")
    blocs = re.split(r"^\s+cle:\s*\"", texte, flags=re.MULTILINE)[1:]
    return [set(re.findall(r'type:\s*"([a-z]+)"', bloc)) for bloc in blocs]


# --- Le signal de page prête tient à l'UI, et cette garde-là manquait (#830) ----------------------
#
# Ces quatre tests-ci ne demandent ni node ni navigateur : ils confrontent les constantes de
# `captures.mjs` aux fichiers qui les rendent. C'est la moitié qui a manqué pendant tout #691→#830 —
# le script attendait « Temps réel connecté », la pastille a été retirée, et RIEN n'a rougi : ni la
# suite de `apps/web` (qui garde son absence, donc allait bien), ni celle-ci (qui ne regardait que
# les décisions du script, contre un stub qui répond toujours). Un signal qui vit des deux côtés
# d'une frontière doit être gardé sur la frontière.

BARRE_SUPERIEURE = RACINE / "apps" / "web" / "components" / "BarreSuperieure.tsx"
SHELL_TSX = RACINE / "apps" / "web" / "components" / "Shell.tsx"


def constante_mjs(nom: str) -> str:
    """La valeur d'une constante chaîne de `captures.mjs` — LUE, jamais recopiée ici.

    Recopier la valeur ferait un test qui se met d'accord avec lui-même : il resterait vert le jour
    où le script change de marqueur sans que l'UI suive, c'est-à-dire le seul jour qui compte.
    """
    trouve = re.search(
        rf'^const {nom} = "([^"]*)";', CAPTURES_MJS.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert trouve is not None, f"{nom} n'est plus une constante chaîne de captures.mjs"
    return trouve.group(1)


def test_le_motif_du_marqueur_distingue_le_rendu_du_commentaire() -> None:
    """Prouver le motif sur l'échantillon fautif AVANT de balayer — et l'échantillon est réel :
    c'est `BarreSuperieure.tsx` d'aujourd'hui, où #691 a laissé son explication.

    Le motif lâche (« la chaîne est quelque part dans le fichier ») aurait répondu « tout va bien »
    après #691 : le texte y est resté, en commentaire, et ailleurs dans `apps/web` en libellé d'un
    écran vide. Seul le motif serré — un nœud de texte JSX, `>…<` — voit la différence entre ce que
    l'application RACONTE et ce qu'elle REND, et c'est celui-là qui aurait attrapé la panne.
    """
    texte = BARRE_SUPERIEURE.read_text(encoding="utf-8")

    assert "Temps réel connecté" in texte, "l'échantillon a changé : #691 ne s'explique plus ici"
    assert ">Temps réel connecté<" not in texte, "le marqueur de #142 serait donc encore rendu ?"
    # …et le motif serré reconnaît bien ce qui EST rendu, sans quoi il refuserait tout.
    assert ">Reconnexion…<" in texte


def test_l_ancre_de_page_prete_est_celle_que_le_shell_pose() -> None:
    """La condition positive du signal : le `<main id>` du shell. Le script en tient une copie —
    c'est une frontière, pas un import —, et la copie doit valoir l'original."""
    ancre = constante_mjs("ANCRE_CONTENU")
    pose = re.search(
        r'^export const ID_CONTENU_PRINCIPAL = "([^"]*)";',
        SHELL_TSX.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert pose is not None, "components/Shell.tsx n'exporte plus ID_CONTENU_PRINCIPAL"
    assert ancre == pose.group(1), (
        f"captures.mjs attend #{ancre} quand le shell pose #{pose.group(1)} : le signal de page "
        "prête ne peut plus arriver, et les parcours filmés ne joueront aucun geste (#830)"
    )


def test_le_marqueur_de_coupure_est_encore_rendu_par_la_barre_superieure() -> None:
    """L'autre condition, celle dont l'ABSENCE dit « temps réel établi ». Elle ne vaut que si le
    marqueur existe : une pastille retirée rend la condition vraie pour toujours, y compris sur une
    application coupée — l'inverse exact de ce qu'on attend d'elle."""
    marqueur = constante_mjs("MARQUEUR_COUPURE")
    assert f">{marqueur}<" in BARRE_SUPERIEURE.read_text(encoding="utf-8"), (
        f"« {marqueur} » n'est plus rendu par components/BarreSuperieure.tsx : le signal de page "
        "prête de captures.mjs est à changer, comme il l'a été à #830 après #691"
    )


def test_l_ancien_marqueur_n_est_plus_une_chaine_du_script() -> None:
    """Le pendant : ce que #691 a retiré ne sert plus de signal. Le garder « au cas où » ramènerait
    la panne — une condition qui ne peut jamais être satisfaite.

    Même partage que partout ailleurs dans ce dépôt : le motif cherche un USAGE (une chaîne JS,
    entre guillemets droits) et jamais une MENTION — `captures.mjs` raconte la panne de #830 en
    prose, avec des guillemets français, et doit pouvoir continuer.
    """
    texte = CAPTURES_MJS.read_text(encoding="utf-8")
    assert "« Temps réel connecté »" in texte, "le script n'explique plus d'où vient son signal"
    assert '"Temps réel connecté"' not in texte, (
        "captures.mjs attend encore, en dur, le marqueur que #691 a retiré de la barre supérieure"
    )


# --- L'autre moitié de la frontière : les textes que les gestes visent (#929) --------------------
#
# Le signal « page prête » n'est pas tout ce que `captures.mjs` attend de l'UI : chaque geste d'un
# parcours s'ancre sur un TEXTE (« jamais de délai fixe pour attendre un état », `parcours.mjs`).
# Une refonte de disposition peut donc laisser le signal intact et vider les parcours de leurs
# gestes — un clip qui se filme, et dans lequel il ne se passe rien. C'est le mode de panne de
# #830, à un cran de là, et c'est ce que les notes techniques de #929 demandaient de vérifier.


def _sans_les_commentaires(source: str) -> str:
    """La source privée de ses lignes de commentaire — un USAGE, jamais une MENTION.

    Le partage du dépôt : la prose d'un fichier cite les libellés qu'elle explique, et un texte qui
    n'existerait plus qu'en commentaire est exactement ce qu'on cherche à voir. Seules les lignes
    ENTIÈREMENT commentées partent : un commentaire de fin de ligne reste, ce qui rend la sonde
    conservatrice — elle dira « présent » une fois de trop plutôt qu'une fois de moins.
    """
    return "\n".join(
        ligne
        for ligne in source.splitlines()
        if not ligne.lstrip().startswith(("//", "*", "/*"))
    )


def _textes_des_parcours() -> list[str]:
    """Les textes que les gestes visent — LUS dans `parcours.mjs`, jamais recopiés."""
    return sorted(
        set(re.findall(r'texte:\s*"([^"]+)"', PARCOURS_MJS.read_text(encoding="utf-8")))
    )


def _vocabulaire_du_front() -> str:
    """Ce que le front écrit, tous commentaires retirés."""
    dossiers = [RACINE / "apps" / "web" / nom for nom in ("app", "components", "lib")]
    return "\n".join(
        _sans_les_commentaires(chemin.read_text(encoding="utf-8"))
        for dossier in dossiers
        for motif in ("*.ts", "*.tsx")
        for chemin in dossier.rglob(motif)
    )


def test_la_sonde_de_vocabulaire_ne_prend_pas_un_commentaire_pour_un_ecran() -> None:
    """Prouver la sonde sur l'échantillon fautif avant de balayer.

    Sans cette moitié, un libellé retiré de l'écran mais resté dans la prose qui l'explique — le
    cas exact de #691, et de la moitié des fichiers de ce dépôt — rendrait « tout va bien » sur la
    question jamais posée.
    """
    fautif = _sans_les_commentaires(
        '// Le bouton disait autrefois "Mettre en pause".\n * "Reprendre" aussi.\n'
        'const libelle = "Approuver";\n'
    )

    assert "Mettre en pause" not in fautif
    assert "Reprendre" not in fautif
    assert "Approuver" in fautif


def test_chaque_geste_d_un_parcours_vise_un_texte_que_le_front_ecrit_encore() -> None:
    """Le balayage. Un parcours dont l'ancre a disparu de l'écran ne joue plus aucun geste, et son
    clip part quand même au manifeste (#545, par construction : un parcours en échec garde sa
    ligne). La présentation d'un milestone montre alors des vidéos immobiles, sans que rien nulle
    part n'ait rougi — c'est précisément ce qui a caché #830 pendant un mois.
    """
    vocabulaire = _vocabulaire_du_front()
    perdus = [texte for texte in _textes_des_parcours() if texte not in vocabulaire]

    assert perdus == [], (
        "scripts/presentation/parcours.mjs vise des textes que apps/web n'écrit plus : "
        + ", ".join(f"« {texte} »" for texte in perdus)
    )


# --- Plus de démo dans ce que les scripts APPELLENT (#1166) ---------------------------------------
#
# Critère 1 : les quatre scripts n'importent plus `maestro.controltower.demo` et ne déclarent plus
# `prj-demo`. Leurs en-têtes racontent encore d'où ils viennent — c'est leur histoire, et une sonde
# qui la prendrait pour un appel rougirait sur une explication. On cherche donc un USAGE : les
# lignes de code, commentaires retirés, jamais une mention (même partage que la sonde du front).

CAPTURES_SH = RACINE / "scripts" / "presentation" / "captures.sh"

#: Ce qui trahirait un retour de la démo dans le code : le module, son fichier, son projet et la
#: variable par laquelle `captures.sh` le passait au navigateur.
DEMO_DANS_LE_CODE = ("maestro.controltower.demo", "demo.py", "prj-demo", "MAESTRO_PROJET_DEMO")


def _code_shell(source: str) -> str:
    """Un script bash privé de ses lignes de commentaire (règle de `_sans_les_commentaires`)."""
    return "\n".join(ligne for ligne in source.splitlines() if not ligne.lstrip().startswith("#"))


def test_la_sonde_d_usage_ne_prend_pas_l_histoire_pour_un_appel() -> None:
    """Prouver la sonde sur l'échantillon fautif avant de balayer, dans les deux sens."""
    raconte = _code_shell(
        "# Jusque-là : python -m maestro.controltower.demo, projet prj-demo.\n"
        '"$PYTHON" -m maestro.controltower.cli --port 1 --etat-banc\n'
    )
    assert not [motif for motif in DEMO_DANS_LE_CODE if motif in raconte]
    assert "--etat-banc" in raconte

    appelle = _code_shell('  nohup "$PYTHON" -m maestro.controltower.demo --port 1 &\n')
    assert "maestro.controltower.demo" in appelle
    declare = _sans_les_commentaires(
        'const PROJET = process.env.MAESTRO_PROJET_DEMO || "prj-demo";'
    )
    assert "prj-demo" in declare and "MAESTRO_PROJET_DEMO" in declare


def test_aucun_script_de_presentation_n_appelle_plus_la_demo() -> None:
    codes = {
        CAPTURES_SH: _code_shell(CAPTURES_SH.read_text(encoding="utf-8")),
        CAPTURES_MJS: _sans_les_commentaires(CAPTURES_MJS.read_text(encoding="utf-8")),
        PARCOURS_MJS: _sans_les_commentaires(PARCOURS_MJS.read_text(encoding="utf-8")),
        # Aucune mention à retirer ici : le texte entier est confronté.
        BUILD_PY: BUILD_PY.read_text(encoding="utf-8"),
    }
    restes = {
        chemin.name: [motif for motif in DEMO_DANS_LE_CODE if motif in code]
        for chemin, code in codes.items()
    }
    assert {nom: motifs for nom, motifs in restes.items() if motifs} == {}, (
        "un script de présentation tourne encore sur la démo"
    )


def test_captures_sh_rouvre_l_etat_du_banc_avant_de_servir_l_api_reelle() -> None:
    """La séquence de `start.sh --etat-banc`, dans l'ordre qui compte : vérifier (et dire l'âge),
    décrire, rouvrir, et SEULEMENT ALORS démarrer l'API — une API démarrée avant la réouverture
    rejouerait l'ancien journal (#1164)."""
    code = _code_shell(CAPTURES_SH.read_text(encoding="utf-8"))
    etapes = [
        "-m maestro.scenarios.etat --verifier",
        "-m maestro.scenarios.etat --decrire",
        "-m maestro.scenarios.etat --rouvrir",
        "-m maestro.controltower.cli --port",
    ]
    positions = [code.find(etape) for etape in etapes]
    assert -1 not in positions, f"étape absente : {etapes[positions.index(-1)]}"
    assert positions == sorted(positions), "l'API réelle démarre avant que l'état soit rouvert"
    ligne_api = next(ligne for ligne in code.splitlines() if etapes[-1] in ligne)
    assert "--etat-banc" in ligne_api, "l'API de captures ne sert pas le jeu de données du banc"
    assert "--api" in code and "--etat" in code, "captures.mjs n'apprend ni l'API ni l'état"


# --- L'API des captures admet l'origine de l'UI qu'elles servent (#1233) --------------------------
#
# #638 a fermé l'API aux origines inconnues, et `captures.sh` lançait la sienne sans lui dire où il
# servait son UI : elle retombait sur l'origine de `start.sh` (:3000) et refusait le navigateur des
# captures (:3010) — dix pages « API injoignable », aucun parcours filmé. La garde lit les deux
# côtés là où le script les écrit (le port de `next start` et l'origine de `--base` ;
# l'environnement qu'il donne à l'API), puis rejoue le préflight du navigateur contre la VRAIE app,
# sur un poste qui a réglé sa propre stack. Aucune liste d'origines n'est recopiée ici : c'est l'API
# qui les résout.

#: Un poste qui a réglé sa propre stack : l'API des captures ne doit rien en hériter.
POSTE_REGLE = {"MAESTRO_PORT_UI": "3000", "MAESTRO_API_ORIGINES": "http://localhost:3000"}

#: Deux lancements fautifs, sur lesquels la garde se prouve : celui d'avant #1233, qui ne dit rien
#: du front, et celui qui ne donne que le port — un `MAESTRO_API_ORIGINES` du poste l'emporterait.
API_SANS_SON_FRONT = {
    "rien": '(cd "$RACINE" && nohup "$PYTHON" -m maestro.controltower.cli --port "$PORT_API"'
    ' --etat-banc >"$LOG_DIR/api.log" 2>&1 &)',
    "le port seul": '(cd "$RACINE" && MAESTRO_PORT_UI="$PORT_UI" nohup "$PYTHON"'
    ' -m maestro.controltower.cli --port "$PORT_API" --etat-banc >"$LOG_DIR/api.log" 2>&1 &)',
}

#: Ce qui repère, dans le script, le lancement de l'API servie.
LANCEMENT_API = "-m maestro.controltower.cli --port"


def _instruction(code: str, marqueur: str) -> str:
    """L'instruction du script qui porte `marqueur`, ses lignes de continuation jointes."""
    return next(ligne for ligne in re.sub(r"\\\n", " ", code).splitlines() if marqueur in ligne)


def _substituer(valeur: str, variables: dict[str, str]) -> str:
    """`$NOM` et `${NOM}` remplacés ; une variable que la garde ne sait pas résoudre la fait
    échouer, plutôt que de juger une valeur qu'elle n'a pas lue."""
    rendu = re.sub(
        r"\$\{(\w+)\}|\$(\w+)",
        lambda m: variables.get(m.group(1) or m.group(2), m.group(0)),
        valeur,
    )
    assert "$" not in rendu, f"valeur que la garde ne sait pas résoudre : {valeur!r}"
    return rendu


def _cote_ui(code: str, port_ui: int) -> tuple[str, str]:
    """La variable du port que `next start` sert, et l'origine que le navigateur des captures
    charge (`--base` de captures.mjs) quand ce port vaut `port_ui`."""
    servie = re.search(r'next start --port "?\$\{?(\w+)', _instruction(code, "next start"))
    assert servie, "captures.sh ne sert plus son UI par `next start --port`"
    base = re.search(r'--base "([^"]+)"', code)
    assert base, "captures.mjs n'apprend plus l'adresse de l'UI"
    return servie.group(1), _substituer(base.group(1), {servie.group(1): str(port_ui)}).rstrip("/")


def _environnement_de_l_api(instruction: str, variables: dict[str, str]) -> dict[str, str]:
    """Les `NOM=valeur` qui préfixent le lancement de l'API, tels que bash les lui passe."""
    mots = shlex.split(instruction)
    affectations = (
        mot.partition("=")
        for mot in mots[: mots.index("-m")]
        if re.fullmatch(r"[A-Za-z_]\w*=.*", mot)
    )
    return {nom: _substituer(valeur, variables) for nom, _, valeur in affectations}


def _preflight(monkeypatch: pytest.MonkeyPatch, api: dict[str, str], origine: str):
    """Le préflight d'un `GET /api/projets` venu de `origine`, tranché par l'app que l'API des
    captures servirait : politique résolue comme en production, sur le poste réglé plus
    l'environnement que le script lui donne."""
    for nom, valeur in {**POSTE_REGLE, **api}.items():
        monkeypatch.setenv(nom, valeur)
    with TestClient(create_app(acces=politique_depuis(load_settings()))) as client:
        return client.options(
            "/api/projets",
            headers={
                "Origin": origine,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )


@pytest.mark.parametrize("lancement", sorted(API_SANS_SON_FRONT))
def test_la_garde_des_origines_attrape_une_api_lancee_sans_son_front(
    monkeypatch: pytest.MonkeyPatch, lancement: str
) -> None:
    """Prouver la garde sur l'échantillon fautif avant de juger le script : chacun rend la panne
    constatée par #1233, un `400` au préflight du navigateur des captures."""
    code = _code_shell(CAPTURES_SH.read_text(encoding="utf-8"))
    variable, origine = _cote_ui(code, 3010)
    api = _environnement_de_l_api(API_SANS_SON_FRONT[lancement], {variable: "3010"})

    reponse = _preflight(monkeypatch, api, origine)

    assert reponse.status_code == 400
    assert "access-control-allow-origin" not in reponse.headers


@pytest.mark.parametrize("port_ui", [3010, 4321])
def test_l_api_des_captures_admet_l_origine_de_l_ui_qu_elles_servent(
    monkeypatch: pytest.MonkeyPatch, port_ui: int
) -> None:
    """Quels que soient les ports choisis : 3010 est le défaut, 4321 un
    `MAESTRO_PORT_UI_CAPTURES` que personne n'a prévu."""
    code = _code_shell(CAPTURES_SH.read_text(encoding="utf-8"))
    variable, origine = _cote_ui(code, port_ui)
    api = _environnement_de_l_api(_instruction(code, LANCEMENT_API), {variable: str(port_ui)})

    reponse = _preflight(monkeypatch, api, origine)

    assert reponse.status_code == 200, f"l'API des captures refuse l'origine de leur UI ({origine})"
    assert reponse.headers["access-control-allow-origin"] == origine


# ==================================================================================================
# 3. Le rendu — `build.py` (#546)
# ==================================================================================================


class Presentation:
    """Un dossier de travail : les fixtures de visuels, le JSON, et l'appel à `build.py`."""

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        racine.mkdir(parents=True, exist_ok=True)
        self.sortie = racine / "presentation.html"

    def png(self, nom: str, taille: int = 128) -> str:
        (self.racine / nom).write_bytes(octets_png(taille))
        return nom

    def webm(self, nom: str, taille: int) -> str:
        (self.racine / nom).write_bytes(octets_webm(taille))
        return nom

    def construire(
        self, donnees: dict, *options: str, **variables: str
    ) -> subprocess.CompletedProcess[str]:
        fichier = self.racine / "presentation.json"
        fichier.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
        environnement = dict(os.environ)
        # Les deux plafonds sont VIDÉS par défaut : un `.env` ou un bloc `env` du poste ne doit pas
        # décider du verdict (même règle que les garde-fous de tests/conftest.py).
        environnement["MAESTRO_PRESENTATION_VIDEO_MAX"] = ""
        environnement["MAESTRO_PRESENTATION_MAX"] = ""
        # Posée sur LE PYTHON QU'ON LANCE, jamais devant un pipeline (#141) : sans elle, ses
        # messages sortent dans l'encodage de la console — cp1252 sous Windows —, et un `⚠ clip
        # sans fichier (Parcours jamais filmé)` fait échouer la LECTURE du test, pas son sujet.
        # Le verdict de la suite ne doit pas dépendre de la locale du poste.
        environnement["PYTHONIOENCODING"] = "utf-8"
        environnement.update(variables)
        return subprocess.run(
            [sys.executable, str(BUILD_PY), str(fichier), "--sortie", str(self.sortie), *options],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environnement,
        )

    def html(self) -> str:
        return self.sortie.read_text(encoding="utf-8")


@pytest.fixture
def presentation(tmp_path: Path) -> Presentation:
    return Presentation(tmp_path / "travail")


def donnees_minimales(**surcharges) -> dict:
    base = {
        "milestone": {
            "titre": "Phase 9 — Résilience",
            "etat": "active",
            "debut": "2026-08-01",
            "echeance": "2026-08-31",
            "resume": "Ce que la phase a changé.",
        },
        "projet": {"url": "https://github.com/compte/depot"},
        "tickets": [],
        "captures": [],
        "ecrans": [],
        "videos": [],
        "notes": [],
    }
    base.update(surcharges)
    return base


def ticket(iid: int, **surcharges) -> dict:
    base = {
        "iid": iid,
        "titre": f"Ticket {iid}",
        "statut": "Terminé",
        "type": "feature",
        "agent": "dev",
        "prio": "moyenne",
        "resume": None,
        "capture": None,
        "ecrans": [],
    }
    base.update(surcharges)
    return base


#: Ce qui charge une ressource dans un document HTML. `href` d'un `<a>` n'en fait pas partie : un
#: lien de ticket VISE la forge, il ne rapatrie rien à l'ouverture — confondre les deux
#: interdirait à la présentation de renvoyer vers le backlog, ce qui est tout son intérêt.
_ATTRIBUTS_DE_CHARGEMENT = ("src", "srcset", "poster", "data", "background")


def references_externes(html: str) -> list[str]:
    """Toute ressource que le document irait chercher ailleurs — la question de l'autonomie."""
    fautives = []
    for attribut in _ATTRIBUTS_DE_CHARGEMENT:
        for valeur in re.findall(rf'\b{attribut}="([^"]*)"', html):
            if not valeur.startswith("data:") and not valeur.startswith("#"):
                fautives.append(f"{attribut}={valeur[:60]}")
    fautives += [f"<link {m[:60]}" for m in re.findall(r"<link\b([^>]*)>", html)]
    fautives += [f"@import {m[:60]}" for m in re.findall(r"@import[^;]*;", html)]
    distant = re.findall(r"url\(\s*['\"]?(?:https?:)?//[^)]*\)", html)
    fautives += [f"url() {m[:60]}" for m in distant]
    return fautives


def test_le_detecteur_de_reference_externe_attrape_un_echantillon_fautif() -> None:
    """Prouver le motif sur un échantillon fautif AVANT de balayer : un ✓ sur une question jamais
    posée ne garde rien (même méthode que `tests/contraste.test.ts`, #534)."""
    assert references_externes('<img src="data:image/png;base64,AAA">') == []
    assert references_externes('<a href="https://github.com/c/d/issues/9">#9</a>') == []
    # Les quatre façons de casser l'autonomie, chacune reconnue pour ce qu'elle est.
    assert references_externes('<img src="https://cdn.example/x.png">')
    assert references_externes('<script src="/vendor.js"></script>')
    assert references_externes('<link rel="stylesheet" href="https://fonts.example/x.css">')
    assert references_externes("<style>@import url(https://fonts.example/x.css);</style>")
    assert references_externes("<style>body{background:url(//cdn.example/x.png)}</style>")


def test_le_html_produit_ne_charge_aucune_ressource_externe(presentation: Presentation) -> None:
    """Le critère : « un seul fichier autonome, partageable tel quel » se vérifie sur les OCTETS
    produits, jamais sur l'intention du gabarit."""
    donnees = donnees_minimales(
        tickets=[ticket(96, capture="couts", ecrans=["couts"], resume="Une phrase.")],
        captures=[{"cle": "couts", "libelle": "Coûts", "fichier": presentation.png("couts.png")}],
        ecrans=[{"cle": "couts", "libelle": "Coûts", "route": "/couts"}],
        videos=[
            {
                "cle": "couts",
                "libelle": "Les coûts en direct",
                "fichier": presentation.webm("couts.webm", 512),
                "affiche": None,
            }
        ],
    )
    processus = presentation.construire(donnees)
    assert processus.returncode == 0, processus.stderr

    html = presentation.html()
    assert references_externes(html) == []
    # Et les visuels sont bien là — un fichier autonome parce qu'il est VIDE ne prouverait rien.
    assert "data:image/png;base64," in html
    assert "data:video/webm;base64," in html
    assert "<video" in html


def test_le_lien_d_un_ticket_vise_la_forge_active(presentation: Presentation) -> None:
    """`projet.url` suit la forge active (`lib.sh host`, #343) : le chemin doit suivre aussi.
    Le `/-/work_items/` hérité de #142 est une route GitLab — 404 sur GitHub."""
    donnees = donnees_minimales(tickets=[ticket(96)])
    assert presentation.construire(donnees).returncode == 0
    html = presentation.html()
    assert 'href="https://github.com/compte/depot/issues/96"' in html
    assert "/-/work_items/" not in html


def test_un_clip_au_dela_du_plafond_par_clip_est_ecarte_et_dit_pourquoi(
    presentation: Presentation,
) -> None:
    """Le repli du plafond de taille, moitié « par clip ». Un clip parti en vrille ne coule pas le
    fichier — et il garde sa place, sous son affiche, plutôt que de laisser un trou."""
    donnees = donnees_minimales(
        captures=[{"cle": "couts", "libelle": "Coûts", "fichier": presentation.png("couts.png")}],
        videos=[
            {
                "cle": "couts",
                "libelle": "Le clip qui déborde",
                "fichier": presentation.webm("gros.webm", 8 * 1024),
                "affiche": None,
            },
            {
                "cle": "chat",
                "libelle": "Le clip qui tient",
                "fichier": presentation.webm("petit.webm", 64),
                "affiche": None,
            },
        ],
    )
    # 1 Kio par clip : le premier déborde, le second passe.
    processus = presentation.construire(donnees, MAESTRO_PRESENTATION_VIDEO_MAX="0.001")
    assert processus.returncode == 0, processus.stderr

    html = presentation.html()
    assert "Clip écarté : " in html
    assert "au-delà du plafond de" in html
    assert "Le clip qui déborde" in html
    # L'affiche de repli : la capture de MÊME CLÉ, trouvée sans que le JSON l'ait déclarée.
    assert 'class="clip-affiche"' in html
    # Le clip qui tient est bien joué — un plafond par clip n'est pas un plafond global.
    assert html.count("<video") == 1
    assert "1/2 intégré(s)" in processus.stderr
    assert "[build] ⚠ clip écarté" in processus.stderr
    # …et le pied de page le redit, là où un lecteur cherche les réserves.
    assert "1 clip(s) sur 2 écartés pour tenir le plafond de taille" in html


def test_un_clip_ecarte_sans_affiche_garde_un_cartouche_plutot_qu_un_trou(
    presentation: Presentation,
) -> None:
    """Sans affiche déclarée et sans capture de même clé, le repli reste VISIBLE."""
    donnees = donnees_minimales(
        videos=[
            {
                "cle": "sans-image",
                "libelle": "Démonstration sans affiche",
                "fichier": presentation.webm("gros.webm", 8 * 1024),
                "affiche": None,
            }
        ],
    )
    assert (
        presentation.construire(donnees, MAESTRO_PRESENTATION_VIDEO_MAX="0.001").returncode == 0
    )
    html = presentation.html()
    assert 'class="clip-absente"' in html
    assert "Démonstration sans affiche" in html


def test_le_budget_du_fichier_est_mesure_sur_la_page_sans_clips(
    presentation: Presentation,
) -> None:
    """L'autre moitié du plafond, et la raison de la première passe : le budget vidéo est ce qui
    RESTE une fois la page pesée. Sans cette mesure, « plafond pour le fichier » ne serait qu'un
    plafond sur les vidéos déguisé, faux dès que les captures pèsent."""
    donnees = donnees_minimales(
        captures=[{"cle": "couts", "libelle": "Coûts", "fichier": presentation.png("couts.png")}],
        videos=[
            {
                "cle": "couts",
                "libelle": "Premier clip",
                "fichier": presentation.webm("a.webm", 256),
                "affiche": None,
            },
            {
                "cle": "chat",
                "libelle": "Second clip",
                "fichier": presentation.webm("b.webm", 256),
                "affiche": None,
            },
        ],
    )
    # 10 Kio pour le fichier entier : la page seule les dépasse déjà, il ne reste RIEN aux clips.
    processus = presentation.construire(donnees, MAESTRO_PRESENTATION_MAX="0.01")
    assert processus.returncode == 0, processus.stderr

    html = presentation.html()
    assert "<video" not in html
    assert html.count("Clip écarté : ") == 2
    assert "budget vidéo du fichier est épuisé" in html
    # Le motif nomme LE BUDGET, pas le plafond par clip : les deux causes ne se soignent pas pareil.
    assert "au-delà du plafond de" not in html
    assert "0/2 intégré(s)" in processus.stderr


def test_un_plafond_a_zero_vaut_aucun_plafond(presentation: Presentation) -> None:
    """Même repli qu'ailleurs dans le dépôt (`MAESTRO_ORCHESTRATE_BUDGET`, `--timeout 0`), et la
    seule façon d'annuler une variable déjà posée par le poste."""
    donnees = donnees_minimales(
        videos=[
            {
                "cle": "gros",
                "libelle": "Un très gros clip",
                "fichier": presentation.webm("gros.webm", 64 * 1024),
                "affiche": None,
            }
        ],
    )
    processus = presentation.construire(
        donnees, MAESTRO_PRESENTATION_VIDEO_MAX="0", MAESTRO_PRESENTATION_MAX="0"
    )
    assert processus.returncode == 0, processus.stderr
    assert "<video" in presentation.html()
    assert "Clip écarté" not in presentation.html()
    assert "sans plafond" in processus.stderr


@pytest.mark.parametrize("valeur", ["beaucoup", "-3"])
def test_un_plafond_illisible_retombe_sur_le_defaut_en_le_disant(
    presentation: Presentation, valeur: str
) -> None:
    """Un plafond silencieusement ignoré est pire qu'un plafond absent : il fait croire à une
    garantie qui n'existe plus."""
    donnees = donnees_minimales(
        videos=[
            {
                "cle": "couts",
                "libelle": "Un clip",
                "fichier": presentation.webm("a.webm", 256),
                "affiche": None,
            }
        ],
    )
    processus = presentation.construire(donnees, MAESTRO_PRESENTATION_VIDEO_MAX=valeur)
    assert processus.returncode == 0, processus.stderr
    assert f"« {valeur} »" in processus.stderr
    assert "plafond par défaut (6 Mio)" in processus.stderr
    # Le défaut s'applique vraiment : le clip, bien plus petit que 6 Mio, est intégré.
    assert "<video" in presentation.html()


def test_un_parcours_en_echec_n_ajoute_aucune_figure(presentation: Presentation) -> None:
    """La jonction entre les deux lots : le manifeste garde la ligne d'un parcours échoué (#545),
    le rendu la traverse sans rien inventer — pas de figure, pas de trou, pas de plantage."""
    donnees = donnees_minimales(
        videos=[
            {"cle": "runs", "libelle": "Parcours jamais filmé", "fichier": None, "affiche": None},
            {
                "cle": "couts",
                "libelle": "Parcours filmé",
                "fichier": presentation.webm("a.webm", 256),
                "affiche": None,
            },
        ],
    )
    processus = presentation.construire(donnees)
    assert processus.returncode == 0, processus.stderr
    assert "clip sans fichier" in processus.stderr

    html = presentation.html()
    assert "Parcours jamais filmé" not in html
    assert "Parcours filmé" in html
    assert html.count("<video") == 1


def test_la_page_dit_que_ses_pieces_viennent_de_la_vraie_stack(
    presentation: Presentation,
) -> None:
    """Le texte de la page suit le changement de #1166 : captures et clips tournent sur la vraie
    Control Tower, dans l'état qu'un passage du banc a laissé — dont la date est dite, parce que la
    page se partage et que son lecteur doit savoir ce qu'il regarde. « Stack de démonstration »
    n'y a plus de sens."""
    clip = {"cle": "couts", "libelle": "Coûts", "affiche": None}
    capture = {"cle": "accueil", "libelle": "Tableau de bord"}
    donnees = donnees_minimales(
        captures=[{**capture, "fichier": presentation.png("accueil.png")}],
        videos=[{**clip, "fichier": presentation.webm("a.webm", 256)}],
        source={"stack": "reelle", "etat": {"passage": "20260922-120842",
                                             "sauve_le": "2026-09-22T09:10:00+00:00"}},
    )
    processus = presentation.construire(donnees)
    assert processus.returncode == 0, processus.stderr
    html = presentation.html()

    assert "stack de démonstration" not in html
    attendu = "dans l'état laissé par le passage des scénarios de référence du 22/09/2026"
    assert html.count(escape(attendu)) == 2, "la galerie ET les clips disent d'où ils viennent"
    assert escape("Captures prises sur la vraie Control Tower") in html
    assert "Tournées sur la vraie Control Tower" in html


@pytest.mark.parametrize(
    "source",
    [None, {}, {"etat": None}, {"etat": {"sauve_le": "hier"}}],
    ids=["absente", "vide", "sans-etat", "date-illisible"],
)
def test_sans_etat_lisible_la_page_dit_la_vraie_stack_sans_date(
    presentation: Presentation, source: Any
) -> None:
    """Une provenance qu'on ne sait pas lire ne s'invente pas : la page dit la stack réelle — ce
    qui reste vrai — et aucune date."""
    donnees = donnees_minimales(
        videos=[{"cle": "couts", "libelle": "Coûts", "affiche": None,
                 "fichier": presentation.webm("a.webm", 256)}],
    )
    if source is not None:
        donnees["source"] = source
    processus = presentation.construire(donnees)
    assert processus.returncode == 0, processus.stderr
    html = presentation.html()
    assert "Tournées sur la vraie Control Tower, jouables ici même." in html
    assert "scénarios de référence du" not in html


def test_sans_clip_la_section_demonstrations_disparait(presentation: Presentation) -> None:
    """Une section vide dessert la présentation autant qu'une vignette hors sujet."""
    assert presentation.construire(donnees_minimales(tickets=[ticket(1)])).returncode == 0
    html = presentation.html()
    assert "section-demonstrations" not in html
    assert "section-ecrans" not in html


def test_les_ecrans_touches_se_lisent_sur_la_carte_et_dans_leur_section(
    presentation: Presentation,
) -> None:
    donnees = donnees_minimales(
        tickets=[ticket(96, ecrans=["couts"]), ticket(97, ecrans=["couts", "chat"])],
        captures=[{"cle": "couts", "libelle": "Coûts", "fichier": presentation.png("couts.png")}],
        ecrans=[
            {"cle": "couts", "libelle": "Coûts", "route": "/couts"},
            {"cle": "chat", "libelle": "Chat", "route": "/chat"},
        ],
    )
    assert presentation.construire(donnees).returncode == 0
    html = presentation.html()
    assert "Écrans touchés par la phase" in html
    assert 'id="ecran-couts"' in html and 'id="ecran-chat"' in html
    # Le poids décroissant : deux tickets ont touché « Coûts », un seul « Chat ».
    assert html.index('id="ecran-couts"') < html.index('id="ecran-chat"')
    assert "2 ticket(s)" in html and "1 ticket(s)" in html
    assert html.count('class="ecran-puce"') == 3


def test_un_ticket_sans_ecran_n_affiche_rien(presentation: Presentation) -> None:
    """« Pas de surface visible, pas de visuel » : l'absence est muette, elle ne se signale pas."""
    donnees = donnees_minimales(tickets=[ticket(96, ecrans=[]), ticket(97, ecrans=None)])
    assert presentation.construire(donnees).returncode == 0
    html = presentation.html()
    # Sur le BALISAGE, pas sur les mots : la feuille de style porte « Écrans touchés » dans un
    # commentaire, et un test qui le chercherait rougirait pour une raison qui n'est pas la sienne.
    assert 'class="carte-ecrans"' not in html
    assert 'id="section-ecrans"' not in html


def test_l_indetermine_est_nomme_et_passe_en_dernier(presentation: Presentation) -> None:
    """Le ranger parmi les écrans le ferait lire COMME un écran, alors qu'il dit l'inverse."""
    donnees = donnees_minimales(
        tickets=[
            ticket(96, ecrans=["-", "-"]),
            ticket(97, ecrans=["-"]),
            ticket(98, ecrans=["chat"]),
        ],
        ecrans=[{"cle": "chat", "libelle": "Chat", "route": "/chat"}],
    )
    assert presentation.construire(donnees).returncode == 0
    html = presentation.html()
    assert "Composants partagés" in html
    assert "commune à plusieurs écrans" in html
    # Deux tickets sur l'indéterminé, un seul sur « Chat » — et pourtant il passe APRÈS.
    assert html.index('id="ecran-chat"') < html.index('id="ecran-indetermine"')
    # Un ticket compte une fois par écran, même s'il le cite deux fois.
    assert "2 ticket(s)" in html


def test_un_ecran_cite_sans_capture_est_rendu_quand_meme(presentation: Presentation) -> None:
    """`/projets` est servi mais hors menu (#280) : la dérivation le nomme, aucune capture ne
    l'illustre. Le taire retirerait de la vue un écran que les commits désignent."""
    donnees = donnees_minimales(
        tickets=[ticket(96, ecrans=["projets"], capture="projets")],
        captures=[],
        ecrans=[],
    )
    assert presentation.construire(donnees).returncode == 0
    html = presentation.html()
    assert 'id="ecran-projets"' in html
    # …sans vignette pour autant : `capture` ne désigne rien qui existe.
    assert 'class="vignette"' not in html


def test_un_json_sans_milestone_est_refuse(presentation: Presentation) -> None:
    processus = presentation.construire({"tickets": []})
    assert processus.returncode == 2
    assert "milestone.titre" in processus.stderr


# ==================================================================================================
# 4. La documentation — ce que la commande fait, et ce qu'elle ne sait pas (#547)
# ==================================================================================================


def manques(texte: str, attendus: tuple[str, ...]) -> list[str]:
    """Les motifs absents du texte — la forme la plus courte d'un balayage prouvable."""
    return [motif for motif in attendus if motif not in texte]


#: Le rattachement se LIT (lot 1) : la commande doit nommer le script qui le dérive et les deux
#: champs qu'il alimente. Sans ces trois-là, la commande décrit encore un pari.
MOTIFS_DERIVATION = (
    "scripts/presentation/ecrans-touches.sh",
    "tickets[].ecrans",
    "tickets[].capture",
)

#: La sélection des parcours filmés, AVEC sa règle d'abstention. La règle est la valeur : sans
#: elle, on retient tout ce qui a été filmé, y compris ce que la phase n'a pas touché.
MOTIFS_PARCOURS = (
    "parcours.mjs",
    "pas de surface visible, pas de visuel",
    "--sans-videos",
)

#: Le résumé de fin annonce les trois choses, séparément — un ✅ global masquerait la cause.
MOTIFS_RESUME = ("clips retenus", "clips écartés", "écrans touchés")

#: Ce que la commande NE SAIT PAS, nommé plutôt que laissé à deviner (critère 3).
MOTIFS_LIMITES = (
    "Un composant partagé ne se rattache à aucune route",
    "Le MCP `chrome-maestro` ne filme pas",
)

#: Ce que `/milestone-presentation` dit de ses pièces depuis #1166 : le réel, l'état qu'il montre,
#: le bloc qui le fait savoir à la page, et le geste qui refait l'état — nommé, jamais joué
#: d'office.
MOTIFS_REEL_PRESENTATION = (
    "l'état du banc",
    "la vraie Control Tower",
    "`source` se **recopie tel quel**",
    "start.sh --etat-banc --rejouer",
    "Ne le lance pas de toi-même",
)

#: Ce que `/milestone-bilan` en dit : même source, et ce qu'une cible absente de l'état veut dire
#: pour un critère (non couvert, avec sa cause — jamais tenu, jamais en défaut par défaut).
MOTIFS_REEL_BILAN = (
    "Ces pièces viennent du réel",
    "dernier passage du banc",
    "pas un défaut du livrable",
    "Les parcours **n'exercent rien**",
)

#: La source d'avant #1166, que ni l'une ni l'autre ne doit plus nommer comme la leur.
DEMO_RETIREE = ("stack de démo", "stack de démonstration")

#: La phrase de l'étape 5 d'avant #543, celle qui envoyait poser une clé de capture au jugé. Sa
#: survivance serait une contradiction dans le même fichier : un prompt est ce que la session lit
#: en dernier, et deux consignes opposées se tranchent par la dernière lue (leçon de #310).
DEVINETTE_RETIREE = "quand la page illustre vraiment le ticket"


def test_les_motifs_de_doc_attrapent_un_echantillon_fautif() -> None:
    """Prouver les motifs sur des textes fabriqués avant de balayer les vrais fichiers.

    Sans cette moitié, les quatre tests suivants rendraient un ✓ sur une question jamais posée —
    c'est la méthode déjà employée par `tests/test_ci_local.py` et `tests/test_cycle_de_vie.py`.
    """
    for famille in (
        MOTIFS_DERIVATION,
        MOTIFS_PARCOURS,
        MOTIFS_RESUME,
        MOTIFS_LIMITES,
        MOTIFS_REEL_PRESENTATION,
        MOTIFS_REEL_BILAN,
    ):
        assert manques(" ".join(famille), famille) == []
        assert manques("", famille) == list(famille)
        # L'oubli le plus probable est celui du DERNIER motif ajouté : il doit ressortir seul.
        assert manques(" ".join(famille[:-1]), famille) == [famille[-1]]


def test_la_commande_lit_le_rattachement_au_lieu_de_le_deviner() -> None:
    texte = COMMANDE.read_text(encoding="utf-8")
    assert manques(texte, MOTIFS_DERIVATION) == [], (
        f"/milestone-presentation ne nomme pas {manques(texte, MOTIFS_DERIVATION)} (#544) : "
        "sans la dérivation, l'agent repose une clé de capture au jugé"
    )
    assert DEVINETTE_RETIREE not in texte, (
        "la consigne de deviner la capture est encore là : elle contredit la dérivation, et "
        "c'est elle que la session lira en dernier"
    )


def test_la_commande_decrit_la_selection_des_parcours_et_son_abstention() -> None:
    texte = COMMANDE.read_text(encoding="utf-8")
    assert manques(texte, MOTIFS_PARCOURS) == [], (
        f"/milestone-presentation ne dit pas {manques(texte, MOTIFS_PARCOURS)} (#545) : "
        "une vidéo hors sujet dessert la présentation deux fois plus qu'une vignette"
    )


def test_le_resume_de_fin_rend_les_clips_retenus_et_ecartes() -> None:
    texte = COMMANDE.read_text(encoding="utf-8")
    # Le résumé vit dans la dernière étape numérotée : c'est là que la règle doit être, pas
    # ailleurs dans le fichier où elle ne serait pas lue au bon moment.
    assert manques(texte, MOTIFS_RESUME) == [], (
        f"le résumé de fin n'annonce pas {manques(texte, MOTIFS_RESUME)}"
    )


def test_la_commande_dit_ce_qu_elle_ne_sait_pas() -> None:
    texte = COMMANDE.read_text(encoding="utf-8")
    assert manques(texte, MOTIFS_LIMITES) == [], (
        f"/milestone-presentation ne nomme pas ses limites {manques(texte, MOTIFS_LIMITES)} : "
        "une limite qu'on ne trouve pas là où l'on travaille se lit comme un oubli"
    )


BILAN = RACINE / ".claude" / "commands" / "milestone-bilan.md"


def test_la_presentation_dit_que_ses_pieces_viennent_du_reel() -> None:
    """Critère 2 de #1166, côté `/milestone-presentation`."""
    texte = COMMANDE.read_text(encoding="utf-8")
    assert manques(texte, MOTIFS_REEL_PRESENTATION) == [], (
        f"/milestone-presentation ne dit pas {manques(texte, MOTIFS_REEL_PRESENTATION)} : "
        "la session présenterait encore ses pièces comme celles d'une démo"
    )


def test_le_bilan_dit_que_ses_pieces_viennent_du_reel() -> None:
    """Critère 2 de #1166, côté `/milestone-bilan` — là où la différence décide d'un verdict."""
    texte = BILAN.read_text(encoding="utf-8")
    assert manques(texte, MOTIFS_REEL_BILAN) == [], (
        f"/milestone-bilan ne dit pas {manques(texte, MOTIFS_REEL_BILAN)}"
    )


@pytest.mark.parametrize("chemin", [COMMANDE, BILAN], ids=lambda c: c.stem)
def test_aucune_commande_ne_tient_plus_ses_pieces_de_la_demo(chemin: Path) -> None:
    texte = chemin.read_text(encoding="utf-8")
    restes = [motif for motif in DEMO_RETIREE if motif in texte]
    assert restes == [], f"{chemin.name} dit encore ses pièces tournées sur la démo : {restes}"


def test_claude_md_decrit_les_quatre_etapes_de_la_presentation() -> None:
    """La doc de l'agent nomme ce qui existe : trois étapes y étaient décrites, il y en a quatre."""
    texte = (RACINE / "CLAUDE.md").read_text(encoding="utf-8")
    bloc = texte.split("Présentations de milestone", 1)[1].split("\n\n", 1)[0]
    assert manques(bloc, ("ecrans-touches.sh", "parcours.mjs", "captures.mjs", "build.py")) == [], (
        "CLAUDE.md décrit encore la présentation d'avant #543"
    )
    assert "chrome-maestro" in bloc, (
        "CLAUDE.md ne dit pas pourquoi les clips ne passent pas par le MCP"
    )


def test_aucun_script_de_presentation_n_est_orphelin() -> None:
    """Les cinq fichiers du dossier sont ceux que la commande et la doc nomment — un script que
    personne n'appelle est un script que personne ne maintient."""
    presents = {p.name for p in (RACINE / "scripts" / "presentation").iterdir() if p.is_file()}
    assert presents == {
        "build.py",
        "captures.mjs",
        "captures.sh",
        "ecrans-touches.sh",
        "parcours.mjs",
    }
    commande = COMMANDE.read_text(encoding="utf-8")
    # `captures.mjs` et `parcours.mjs` sont appelés PAR `captures.sh` : la commande n'a pas à
    # invoquer les cinq, mais elle doit dire d'où viennent les parcours qu'on lui demande de trier.
    for nom in ("build.py", "captures.sh", "ecrans-touches.sh", "parcours.mjs"):
        assert nom in commande, f"{nom} n'est nommé nulle part dans /milestone-presentation"


# --- La visionneuse : toute image s'ouvre en grand (#563) -----------------------------------------


#: Un déclencheur enveloppant DIRECTEMENT une image de contenu. Le lien et le bouton sont les deux
#: seules balises admises : toutes deux sont focusables et s'actionnent au clavier sans une ligne
#: de JS, là où un `<div>` cliquable serait invisible à qui n'a pas de souris.
_ENVELOPPE = re.compile(r'<(?:a|button)\b[^>]*\bdata-agrandir\b[^>]*>\s*<img\b[^>]*src="data:')

#: Les images de contenu — celles qui portent des octets. L'`<img>` de la visionneuse, laissée vide
#: dans le document et remplie par le script, n'en fait pas partie : c'est tout le sujet.
_IMAGE_DE_CONTENU = re.compile(r'<img\b[^>]*src="data:')


def test_le_detecteur_d_enveloppe_attrape_un_echantillon_fautif() -> None:
    """Prouver le motif sur un cas fautif AVANT de balayer — sans quoi le test qui suit rendrait un
    ✓ sur une question jamais posée (même méthode que `references_externes`, #534)."""
    nue = '<figure><img src="data:image/png;base64,AAA"></figure>'
    assert _IMAGE_DE_CONTENU.findall(nue), "l'échantillon ne contient même pas d'image de contenu"
    assert _ENVELOPPE.findall(nue) == [], "une image NUE est comptée comme enveloppée"
    # Un déclencheur qui n'est ni lien ni bouton ne compte pas davantage : il ne s'actionne pas au
    # clavier, et c'est ce que le motif doit refuser.
    muet = '<div data-agrandir><img src="data:image/png;base64,AAA"></div>'
    assert _ENVELOPPE.findall(muet) == []
    # Le cas conforme, lui, est reconnu sous ses deux formes.
    assert _ENVELOPPE.findall('<a data-agrandir href="#x"><img src="data:image/png;base64,A">')
    assert _ENVELOPPE.findall('<button type="button" data-agrandir><img src="data:image/png,A">')


def donnees_aux_quatre_origines(presentation: Presentation) -> dict:
    """Un jeu qui produit UNE image par endroit du gabarit qui en rend une : vignette de carte,
    écran touché, galerie, et affiche de repli d'un clip écarté."""
    return donnees_minimales(
        tickets=[ticket(96, capture="couts", ecrans=["couts"])],
        captures=[{"cle": "couts", "libelle": "Coûts", "fichier": presentation.png("couts.png")}],
        ecrans=[{"cle": "couts", "libelle": "Coûts", "route": "/couts"}],
        videos=[
            {
                "cle": "runs",
                "libelle": "Les runs",
                "fichier": presentation.webm("runs.webm", 4096),
                "affiche": presentation.png("affiche.png"),
            }
        ],
    )


def test_toute_image_de_la_page_s_ouvre_en_grand(presentation: Presentation) -> None:
    """Le critère du ticket, mesuré sur les OCTETS produits. Compter les déclencheurs ne suffirait
    pas : c'est l'ÉGALITÉ avec les images qui dit qu'aucune n'a été oubliée en chemin."""
    # Le plafond minuscule écarte le clip — c'est ce qui fait rendre son affiche de repli, la
    # quatrième et la plus facile à oublier des origines d'image.
    processus = presentation.construire(
        donnees_aux_quatre_origines(presentation), MAESTRO_PRESENTATION_VIDEO_MAX="0.001"
    )
    assert processus.returncode == 0, processus.stderr
    html = presentation.html()

    images = _IMAGE_DE_CONTENU.findall(html)
    assert len(images) == 4, f"les quatre origines d'image ne sont pas toutes rendues : {images}"
    assert len(_ENVELOPPE.findall(html)) == len(images), (
        "une image de contenu n'est pas enveloppée dans un déclencheur de visionneuse"
    )
    # Chaque déclencheur est NOMMÉ par l'action qu'il déclenche, et non par le contenu de l'image
    # (que l'`alt` dit déjà) : sans ça, un lecteur d'écran annonce deux fois la même chose et
    # jamais ce qu'un appui va faire.
    assert html.count('aria-label="Agrandir') == len(images)


def test_la_visionneuse_n_encode_aucune_image_une_seconde_fois(presentation: Presentation) -> None:
    """La page vit sous un plafond de taille : la visionneuse doit être gratuite en octets. Elle
    l'est parce qu'elle réutilise la source de la vignette au lieu d'embarquer la sienne."""
    processus = presentation.construire(donnees_aux_quatre_origines(presentation))
    assert processus.returncode == 0, processus.stderr

    vue = re.search(r'<img\b[^>]*\bclass="visionneuse-image"[^>]*>', presentation.html())
    assert vue, "la visionneuse n'a pas d'image"
    assert "src=" not in vue.group(0), (
        "l'image de la visionneuse porte une source dans le document — elle doit être remplie"
        " par le script, depuis la vignette cliquée"
    )


def test_la_visionneuse_s_appuie_sur_le_dialog_natif(presentation: Presentation) -> None:
    """`Échap`, le piège de focus et le retour du focus au déclencheur sont NATIFS à un `<dialog>`
    ouvert en modal. Les réécrire à la main serait moins sûr : le test garde donc le choix de la
    balise, et non une implémentation de rechange."""
    processus = presentation.construire(donnees_aux_quatre_origines(presentation))
    assert processus.returncode == 0, processus.stderr
    html = presentation.html()

    assert re.search(r'<dialog\b[^>]*\bid="visionneuse"', html), (
        "la visionneuse n'est pas un <dialog> — Échap et le piège de focus sont alors à écrire"
    )
    assert "showModal()" in html, "ouverte hors du mode modal : ni piège de focus, ni ::backdrop"
    # Fermeture au clic hors de l'image, et par un bouton explicitement nommé.
    assert 'aria-label="Fermer' in html
    assert "dialogue.close()" in html


def test_l_animation_de_la_visionneuse_n_existe_pas_sous_mouvement_reduit(
    presentation: Presentation,
) -> None:
    """Elle n'est pas neutralisée après coup : elle n'est DÉCLARÉE que sous `no-preference`. Une
    surcharge qui vient après se contourne par n'importe quelle règle plus spécifique ; une
    déclaration absente, non."""
    processus = presentation.construire(donnees_minimales())
    assert processus.returncode == 0, processus.stderr
    html = presentation.html()

    bloc = re.search(
        r"@media \(prefers-reduced-motion: no-preference\) \{(.*?)\n  \}", html, re.DOTALL
    )
    assert bloc, "aucun bloc `no-preference` : l'animation joue pour tout le monde"
    assert "animation:" in bloc.group(1)
    dehors = html.replace(bloc.group(0), "")
    assert not re.search(r"\.visionneuse[^{]*\{[^}]*animation:", dehors), (
        "une animation de la visionneuse est déclarée hors du bloc `no-preference`"
    )


# --- Le pied de page ne porte que les échecs de génération (#563) ---------------------------------


def pied_de(html: str) -> str:
    return html.split('<footer class="pied">', 1)[1].split("</footer>", 1)[0]


def test_sans_note_le_pied_de_page_n_a_pas_de_liste(presentation: Presentation) -> None:
    """Le cas nominal : rien n'a manqué, donc le pied ne porte que sa ligne de provenance. Des
    réserves méthodologiques y coûteraient la fin du document au lecteur à qui la page est
    destinée — elles appartiennent au résumé rendu dans le terminal."""
    processus = presentation.construire(donnees_minimales())
    assert processus.returncode == 0, processus.stderr

    pied = pied_de(presentation.html())
    assert "<ul>" not in pied, "le pied de page porte une liste alors que rien n'a manqué"
    assert "Généré le" in pied


def test_une_note_de_generation_est_rendue_dans_le_pied(presentation: Presentation) -> None:
    """La mécanique reste : ce qui a MANQUÉ à cette génération-ci doit se lire sur la page, son
    lecteur n'ayant aucun moyen de le deviner en la regardant."""
    manque = "Captures indisponibles : l'état du banc n'a pas pu être rouvert."
    processus = presentation.construire(donnees_minimales(notes=[manque]))
    assert processus.returncode == 0, processus.stderr

    pied = pied_de(presentation.html())
    assert "<ul>" in pied
    assert "du banc n" in pied and "a pas pu être rouvert" in pied


def test_la_commande_borne_ce_que_les_notes_acceptent() -> None:
    """Le gabarit ne peut pas distinguer une réserve de production d'un échec de génération : c'est
    le prompt qui tranche, et c'est donc lui qu'on garde."""
    morceaux = COMMANDE.read_text(encoding="utf-8").split("`notes` ne porte que", 1)
    assert len(morceaux) == 2, "/milestone-presentation ne dit plus ce que `notes` accepte"
    regle = morceaux[1].split("\n\n", 1)[0]
    assert "Ce que la commande ne sait pas" in regle, (
        "la règle ne renvoie pas les limites méthodologiques vers le résumé du terminal"
    )


# --------------------------------------------------------------------------------------------
# L'ouverture de la présentation à la fin (#670)
#
# Ces tests-ci appellent `principal()` EN PROCESSUS, là où tout le reste du fichier passe par un
# sous-processus. Ce n'est pas une inconséquence : `webbrowser.open()` essaie les navigateurs de
# `_tryorder` **l'un après l'autre** et ne s'arrête qu'au premier qui répond `True`. Un `BROWSER`
# bidon dans l'environnement ne neutralise donc rien — il échoue, puis la chaîne **retombe sur le
# navigateur par défaut du poste**, et la suite ouvrirait une vraie fenêtre à chaque exécution.
# Le seul point où l'ouvreur se neutralise vraiment est l'appel lui-même.
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def build() -> Any:
    """`build.py` en module — il ne vit dans aucun paquet, d'où le chargement par chemin."""
    spec = importlib.util.spec_from_file_location("maestro_build_presentation", BUILD_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def generer(build: Any, tmp_path: Path, *options: str) -> tuple[int, Path]:
    donnees = tmp_path / "presentation.json"
    donnees.write_text(json.dumps(donnees_minimales(), ensure_ascii=False), encoding="utf-8")
    # Sous un sous-dossier qui n'existe pas encore : l'ouverture doit viser le fichier tel qu'il a
    # été écrit, pas le chemin tel qu'il a été demandé.
    sortie = tmp_path / "sous-dossier" / "presentation.html"
    return build.principal([str(donnees), "--sortie", str(sortie), *options]), sortie


def ouvreur_espion(build: Any, monkeypatch: pytest.MonkeyPatch, reponse: Any = True) -> list[str]:
    """Remplace l'ouvreur par un mouchard. `reponse` peut être une exception, qui sera levée."""
    appels: list[str] = []

    def faux_open(adresse: str, *_args: Any, **_kwargs: Any) -> bool:
        appels.append(adresse)
        if isinstance(reponse, BaseException):
            raise reponse
        return reponse

    monkeypatch.setattr(build.webbrowser, "open", faux_open)
    return appels


def test_sans_l_option_aucune_ouverture_n_est_tentee(
    build: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le script est appelé hors de la commande (rejeu à la main, autre script, CI) : une fenêtre
    qui s'ouvre sans qu'on l'ait demandée est une régression, pas un service."""
    appels = ouvreur_espion(build, monkeypatch)

    code, sortie = generer(build, tmp_path)

    assert code == 0
    assert sortie.exists()
    assert appels == [], "build.py a ouvert un navigateur sans qu'on le lui demande"
    assert "ouvert" not in capsys.readouterr().out


def test_l_option_ouvre_le_fichier_qui_vient_d_etre_ecrit(
    build: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ce qui s'ouvre doit être le fichier RÉELLEMENT écrit — `--sortie` le déplace — et l'adresse
    passe en `file:///` : seule forme qui traverse un chemin Windows à lettre de lecteur et à
    espaces sans se faire réinterpréter."""
    appels = ouvreur_espion(build, monkeypatch)

    code, sortie = generer(build, tmp_path, "--ouvrir")

    assert code == 0
    assert appels == [sortie.resolve().as_uri()]
    assert appels[0].startswith("file:///")
    assert "ouverte dans le navigateur par défaut" in capsys.readouterr().out


def test_une_ouverture_qui_echoue_ne_change_ni_le_code_de_retour_ni_le_fichier(
    build: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le cœur du ticket : l'écriture est le livrable, l'ouverture un confort. Une génération
    réussie ne devient jamais un échec faute d'avoir pu ouvrir une fenêtre — même statut que
    `sync-main` ou que l'écriture de l'audit en fin de run."""
    ouvreur_espion(build, monkeypatch, reponse=False)

    code, sortie = generer(build, tmp_path, "--ouvrir")

    assert code == 0, "une ouverture en échec a fait échouer une génération réussie"
    assert "<html" in sortie.read_text(encoding="utf-8")
    flux = capsys.readouterr()
    assert "présentation écrite" in flux.out
    assert "ouverture impossible" in flux.err
    assert sortie.resolve().as_uri() in flux.err, (
        "un échec qui ne nomme pas ce qu'il a tenté d'ouvrir n'apprend rien"
    )


def test_un_ouvreur_qui_leve_est_rattrape_comme_un_ouvreur_qui_refuse(
    build: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`webbrowser.open` ne se contente pas de rendre `False` : il lève sur un poste sans session
    graphique. Les deux échecs doivent coûter la même chose — rien."""
    ouvreur_espion(build, monkeypatch, reponse=RuntimeError("pas de session graphique"))

    code, sortie = generer(build, tmp_path, "--ouvrir")

    assert code == 0
    assert sortie.exists()
    erreur = capsys.readouterr().err
    assert "ouverture impossible" in erreur and "pas de session graphique" in erreur


def blocs_de_code(markdown: str) -> str:
    """Le contenu des blocs ```…``` d'un markdown, recollé — la prose reste dehors."""
    dedans = False
    lignes: list[str] = []
    for ligne in markdown.splitlines():
        if ligne.lstrip().startswith("```"):
            dedans = not dedans
            continue
        if dedans:
            lignes.append(ligne)
    return "\n".join(lignes)


def test_la_commande_passe_l_option_et_ne_porte_aucune_recette_d_ouverture() -> None:
    """Règle de #310, gardée aussi par `tests/test_ci_local.py` : une recette recopiée dans un
    prompt fige le comportement au jour où elle a été écrite et n'est testable par personne. La
    logique de plateforme vit dans le script ; le prompt ne fait que passer l'option."""
    recettes = ("Start-Process", "xdg-open", "os.startfile", "webbrowser.open", "cmd //c start")

    # Le motif cherche un USAGE, jamais une MENTION — le prompt NOMME ces commandes en prose,
    # précisément pour interdire de les écrire, et un motif qui ne ferait pas la différence
    # obligerait à retirer soit l'interdiction, soit la garde. Ne balayer que les blocs de code
    # est ce qui les sépare, et les DEUX moitiés se prouvent avant de conclure de l'absence.
    recette = "Puis ouvre le fichier :\n```\npowershell -c Start-Process presentation.html\n```"
    mention = "N'écris jamais la commande toi-même (`Start-Process`, `xdg-open`…)."
    assert [r for r in recettes if r in blocs_de_code(recette)] == ["Start-Process"]
    assert [r for r in recettes if r in blocs_de_code(mention)] == []

    texte = COMMANDE.read_text(encoding="utf-8")
    assert "--ouvrir" in texte, "/milestone-presentation ne passe plus l'option d'ouverture"
    trouvees = [recette for recette in recettes if recette in blocs_de_code(texte)]
    assert trouvees == [], f"recette d'ouverture réintroduite dans le prompt : {trouvees}"
