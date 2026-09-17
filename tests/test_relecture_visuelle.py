"""Tests de la relecture visuelle — le geste qui regarde, et ce qui l'appelle (#932, #935).

La chaîne visuelle de [docs/30 §5.1](../docs/30-cible-visuelle-control-tower.md) sait **viser**
(`/design-veille`), **tenir** (tokens, primitives) et **garder** (contraste, a11y, sobriété,
géométrie). Personne n'y **regardait** : une session pouvait écrire une interface, voir tous ses
tests verts, et n'avoir jamais ouvert l'écran qu'elle venait de changer. Le lot 2 (#932) a rendu le
geste jouable (docs/30 §5.6), le lot 5 (#935) l'a rendu **obligatoire à la clôture** (§5.5).

Ce que ce module garde, et **aucun de ces contrôles n'est le verdict** — ce qui est outillé est la
désignation de ce qu'il y a à regarder, jamais le jugement (partage de #562, #612 et #714) :

* **le plan** — quels écrans, sur quels ports, et le fait qu'un ticket sans surface visible rende
  `3` sans rien démarrer. C'est ce `3` qui rend le déclencheur gratuit sur les neuf dixièmes du
  backlog, donc ce qui rend l'étape 4bis de `/ticket-finish` tenable ;
* **la remonte par les imports** — la question que #544 ne pose pas (« quels écrans *affichent* ce
  composant ? »), et le composant qui ne mène nulle part, **nommé** plutôt que perdu ;
* **les ports** — lus dans le `settings.local.json` du dépôt courant et **jamais** dans
  l'environnement, qui ment précisément là où ce script sert : une session relocalisée par
  `/ticket-start` garde les ports du clone principal (docs/10 §9.1) ;
* **ce qui est laissé derrière** — rien, ni sur un plan vide, ni sur une stack qui ne démarre pas ;
* **le verbe** (`lib.sh relecture-note`) — l'ancre, l'idempotence, la distinction jouée / non jouée,
  et ses refus qui tombent **avant** toute écriture ;
* **le déclencheur** — `/ticket-finish` joue au lieu de demander, **à l'identique en run et en
  interactif**, **avant** le filet CI, et ne recopie pas la séquence du skill. Avec son
  **silence** : sur un ticket sans écran l'étape ne dit rien et n'écrit rien, ce qui est la
  moitié la plus jouée du mécanisme et la plus facile à rendre bavarde.

Chaque contrôle qui conclut d'une **absence** porte son contre-exemple : sans cette moitié, un motif
mal branché rendrait un ✓ sur une question jamais posée (méthode de #366, #537, #578).

⚠ **Trois surfaces du même chantier vivent ailleurs, et ce n'est pas un oubli** — un second garde
sur la même question serait le premier moyen d'en laisser un se périmer sans qu'on le remarque :

* les deux **sources** ajoutées à `ecrans-touches.sh` (`--travail-en-cours`, `--chemins`) sont
  gardées dans [`test_presentation.py`](test_presentation.py), là où vit la règle de #544 qu'elles
  étendent — la recopier ici reviendrait à accepter que deux appelants finissent par ne plus nommer
  le même écran pour le même fichier ;
* l'**accès web** ouvert par #933 et la **veille jouée en run** de #934 sont gardés dans
  [`test_design_veille.py`](test_design_veille.py) ;
* **G5** — `merge-mr` et `pipeline-wait` refusés en run, qui ressemblent à un trou et sont
  l'inverse — est gardé en entier (règle, rapport, doc) par
  [`test_ecart_run.py`](test_ecart_run.py). Ce module ne vérifie ici qu'une chose : que
  l'ouverture de la relecture n'a pas débordé dessus.

⚠ **Aucune stack n'est montée ici.** `scripts/controltower/start.sh` est un double qui journalise ce
qu'on lui demande — même raison que le `docker` neutralisé de `harnais_forge.py` : ce qui se teste
est la **décision** de le lancer, ses arguments et ses ports, jamais le fait que Next démarre.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from harnais_forge import BASH, GIT, RACINE, Depot, ecritures, monte_depot

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

RELECTURE_SH = RACINE / "scripts" / "design" / "relecture-visuelle.sh"
ECRANS_TOUCHES = RACINE / "scripts" / "presentation" / "ecrans-touches.sh"
SKILL = RACINE / ".claude" / "skills" / "relecture-visuelle" / "SKILL.md"
PROMPT_FINISH = RACINE / ".claude" / "commands" / "ticket-finish.md"
PROMPT_SHIP = RACINE / ".claude" / "commands" / "ticket-ship.md"
REGLAGES_RUN = RACINE / "scripts" / "orchestrate" / "settings.run.json"
REGLAGES_DEPOT = RACINE / ".claude" / "settings.json"

#: Le double de `start.sh` : il journalise la commande ET les ports reçus — les deux sont la
#: décision à garder —, et son code de retour se pilote pour jouer la stack qui ne démarre pas.
FAUX_START = """#!/usr/bin/env bash
racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$racine/.maestro"
printf '%s\\tapi=%s\\tui=%s\\n' "$*" "${MAESTRO_PORT_API:-}" "${MAESTRO_PORT_UI:-}" \\
  >>"$racine/.maestro/start.log"
exit "${MAESTRO_FAUX_START_CODE:-0}"
"""


class DepotRelecture:
    """Un dépôt jetable où `relecture-visuelle.sh` se croit chez lui.

    Le script résout sa racine par `$(dirname $BASH_SOURCE)/../..` et tout le reste en dérive —
    `ecrans-touches.sh`, `demo.py`, `.claude/settings.local.json`, `apps/web/`, `core/projets/`.
    Le recopier sous `scripts/design/` du dépôt d'essai suffit donc à le faire travailler là, sans
    rien injecter : c'est la même mécanique qu'en production.
    """

    def __init__(self, racine: Path, *, projet: str = "prj-demo") -> None:
        self.racine = racine
        self.projet = projet
        racine.mkdir(parents=True)
        self._git("init", "-b", "main")
        # Identité LOCALE : l'image du job pytest n'en pose aucune globalement, et c'est délibéré
        # (#333 — une identité globale remasquerait le bug qu'elle a fait sortir).
        self._git("config", "user.email", "essai@maestro.test")
        self._git("config", "user.name", "Essai Maestro")

        for source, relatif in (
            (RELECTURE_SH, "scripts/design/relecture-visuelle.sh"),
            (ECRANS_TOUCHES, "scripts/presentation/ecrans-touches.sh"),
        ):
            cible = racine / relatif
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, cible)

        self.ecris("scripts/controltower/start.sh", FAUX_START)
        # L'identifiant du projet de démo est LU dans le scénario, jamais recopié : le jour où la
        # démo change de projet, le script suit. Le décor le prouve en le déplaçant.
        self.ecris("maestro/controltower/demo.py", f'PROJET_ID = "{projet}"\n')
        self._git("add", "-A")
        self._git("commit", "-m", "chore: squelette\n\nRefs #1")

    # --- décor -------------------------------------------------------------------------------
    def ecris(self, relatif: str, contenu: str) -> None:
        fichier = self.racine / relatif
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(contenu, encoding="utf-8", newline="\n")

    def ports(self, api: int, ui: int) -> None:
        """Le `settings.local.json` que `worktree.sh` écrit au montage — la source de vérité."""
        self.ecris(
            ".claude/settings.local.json",
            json.dumps(
                {"env": {"MAESTRO_PORT_API": str(api), "MAESTRO_PORT_UI": str(ui)}}, indent=2
            )
            + "\n",
        )

    def commit(self, message: str, chemins: list[str]) -> None:
        for chemin in chemins:
            self.ecris(chemin, f"// {chemin}\n")
            self._git("add", "--", chemin)
        self._git("commit", "-m", message)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert GIT is not None
        return subprocess.run(  # noqa: S603
            [GIT, "-c", "core.hooksPath=", *args],
            cwd=self.racine,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    # --- exécution ---------------------------------------------------------------------------
    def joue(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        # Le poste ne décide de rien : les deux ports qu'une session porterait sont retirés, pour
        # que ce que le test observe vienne du décor et non de la machine (règle du conftest).
        for cle in ("MAESTRO_PORT_API", "MAESTRO_PORT_UI"):
            environnement.pop(cle, None)
        environnement.update(env or {})
        assert BASH is not None
        return subprocess.run(  # noqa: S603
            [BASH, "scripts/design/relecture-visuelle.sh", *args],
            cwd=self.racine,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environnement,
        )

    def appels_start(self) -> list[str]:
        journal = self.racine / ".maestro" / "start.log"
        if not journal.exists():
            return []
        return [ligne for ligne in journal.read_text(encoding="utf-8").splitlines() if ligne]


@pytest.fixture
def depot(tmp_path: Path) -> DepotRelecture:
    return DepotRelecture(tmp_path / "depot")


def tsv(sortie: str) -> list[tuple[str, ...]]:
    """Les lignes de données du plan en TSV — l'en-tête `#` est ignorable, comme pour #544."""
    return [
        tuple(ligne.split("\t"))
        for ligne in sortie.splitlines()
        if ligne and not ligne.startswith("#")
    ]


# =================================================================================================
# Le plan — ce qu'il y a à regarder, et le fait qu'il ne démarre rien pour le dire
# =================================================================================================


def test_sans_surface_visible_le_plan_sabstient_et_ne_monte_rien(depot: DepotRelecture) -> None:
    """L'abstention nominale, et c'est elle qui rend le déclencheur de #935 gratuit.

    Un ticket de moteur, de CI ou de doc n'a pas d'écran : `3` en deux secondes, pas une panne. Si
    ce chemin coûtait une stack, l'étape 4bis de `/ticket-finish` serait payée par tout le backlog.
    """
    depot.commit("feat: moteur\n\nRefs #40", ["maestro/engine.py"])
    resultat = depot.joue("--plan", "40")
    assert resultat.returncode == 3, resultat.stdout + resultat.stderr
    assert "aucun écran" in resultat.stdout
    assert depot.appels_start() == [], "le plan ne démarre rien, c'est sa raison d'être"


def test_le_plan_nomme_les_ecrans_touches_avec_leur_url(depot: DepotRelecture) -> None:
    depot.ports(8036, 3036)
    depot.commit("feat: coûts\n\nRefs #41", ["apps/web/app/couts/page.tsx"])
    resultat = depot.joue("--plan", "41")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert "/couts" in resultat.stdout
    assert "http://localhost:3036/couts" in resultat.stdout


def test_le_travail_non_commite_compte_autant_que_les_commits(depot: DepotRelecture) -> None:
    """La relecture a lieu AVANT la clôture : le ticket n'a souvent rien de commité.

    C'est toute la raison de `--travail-en-cours` (#932 sur #544), et le contre-exemple est dans le
    décor — ce fichier n'est dans aucun commit.
    """
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue("--plan", "42")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert "/runs" in resultat.stdout


def test_un_composant_partage_remonte_vers_les_ecrans_qui_laffichent(
    depot: DepotRelecture,
) -> None:
    """La question que #544 ne pose pas — et sans laquelle le geste serait muet sur une bonne part
    des tickets d'interface, `components/` étant l'endroit le plus édité de `apps/web`.

    Le plan dit **par quel fichier** l'écran est arrivé : un « ← Conversation.tsx » dit quoi
    regarder, là où nommer l'importateur rendrait la réponse au lieu de la cause.
    """
    depot.ecris(
        "apps/web/app/chat/page.tsx",
        'import { Conversation } from "@/components/Conversation";\n',
    )
    depot.ecris("apps/web/app/couts/page.tsx", "// sans rapport\n")
    depot._git("add", "-A")
    depot._git("commit", "-m", "chore: écrans\n\nRefs #1")
    depot.ecris("apps/web/components/Conversation.tsx", "// retouché\n")

    resultat = depot.joue("--plan", "--tsv", "43")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    # La cinquième colonne est l'avant (#977) : `-` ici, le dépôt d'essai n'ayant pas d'origin/main.
    assert tsv(resultat.stdout) == [
        ("/chat", "http://localhost:3000/chat", "via", "apps/web/components/Conversation.tsx", "-")
    ], "l'écran qui AFFICHE le composant, et le composant nommé comme origine"


def test_un_composant_que_personne_naffiche_est_nomme_jamais_devine(
    depot: DepotRelecture,
) -> None:
    """L'inconnu se nomme (règle de #544) : fondu dans une remonte collective, il disparaîtrait.

    Un `Shell.tsx` importé du seul `layout.tsx`, une primitive que personne n'affiche encore — la
    session le relaie dans « ce que je n'ai pas pu voir », qui est une réponse et non un trou.
    """
    depot.ecris("apps/web/components/Orpheline.tsx", "// personne ne m'importe\n")
    resultat = depot.joue("--plan", "--tsv", "44")
    assert tsv(resultat.stdout) == [
        ("-", "-", "indetermine", "apps/web/components/Orpheline.tsx", "-")
    ]
    assert resultat.returncode == 3, "aucune route : rien à ouvrir dans un navigateur"
    assert depot.appels_start() == []


def test_un_indetermine_ne_decide_pas_du_code_de_retour(depot: DepotRelecture) -> None:
    """Deux natures dans un seul flux : les ÉCRANS décident, les indéterminés accompagnent.

    Un `layout.tsx` n'appartient à aucune route et ne s'ouvre pas — mais il est dit, parce que le
    taire reviendrait à prétendre qu'il n'a rien changé à l'écran.
    """
    depot.ecris("apps/web/app/layout.tsx", "// la coquille de tous les écrans\n")
    resultat = depot.joue("--plan", "45")
    assert resultat.returncode == 3
    assert "indéterminé" in resultat.stdout
    assert "apps/web/app/layout.tsx" in resultat.stdout


def test_direct_lemporte_sur_via(depot: DepotRelecture) -> None:
    """Le rattachement le plus sûr gagne, et les deux fichiers restent nommés : une seule ligne.

    Sans cette règle, le même écran apparaîtrait deux fois dans le plan — une fois parce qu'il a été
    touché, une fois parce qu'il affiche un composant touché.
    """
    depot.ecris(
        "apps/web/app/chat/page.tsx",
        'import { Conversation } from "@/components/Conversation";\n',
    )
    depot._git("add", "-A")
    depot._git("commit", "-m", "chore: écran\n\nRefs #1")
    depot.ecris(
        "apps/web/app/chat/page.tsx",
        'import { Conversation } from "@/components/Conversation";\n// retouché\n',
    )
    depot.ecris("apps/web/components/Conversation.tsx", "// retouché aussi\n")

    lignes = tsv(depot.joue("--plan", "--tsv", "46").stdout)
    assert len(lignes) == 1, lignes
    route, _url, origine, fichiers, _avant = lignes[0]
    assert (route, origine) == ("/chat", "direct")
    assert "apps/web/app/chat/page.tsx" in fichiers
    assert "apps/web/components/Conversation.tsx" in fichiers


# =================================================================================================
# Les ports — le fichier du worktree, jamais l'environnement
# =================================================================================================
# C'est le piège que ce script existe en partie pour éviter : une session relocalisée par
# `/ticket-start` garde dans son bloc `env` les ports du CLONE PRINCIPAL (docs/10 §9.1 —
# `EnterWorktree` ne réévalue que les caches liés au CWD). Suivre l'environnement, c'est arrêter la
# stack d'à côté ; viser 8000/3000 en dur, c'est la même faute en pire.


def test_le_fichier_du_depot_lemporte_sur_lenvironnement(depot: DepotRelecture) -> None:
    depot.ports(8036, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue(
        "--plan", "47", env={"MAESTRO_PORT_API": "8000", "MAESTRO_PORT_UI": "3000"}
    )
    assert "UI 3036 · API 8036" in resultat.stdout, (
        "l'environnement d'une session relocalisée porte les ports du clone principal : les suivre "
        "arrêterait la stack d'une session voisine (#932, docs/10 §9.1)"
    )
    assert "3000" not in resultat.stdout


def test_sans_fichier_lenvironnement_est_le_repli(depot: DepotRelecture) -> None:
    """Le contre-exemple du test précédent : sans lui, « le fichier gagne » ne prouverait rien —
    la valeur pourrait venir d'un défaut en dur."""
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue(
        "--plan", "48", env={"MAESTRO_PORT_API": "8071", "MAESTRO_PORT_UI": "3071"}
    )
    assert "UI 3071 · API 8071" in resultat.stdout


def test_sans_rien_le_repli_du_repli_est_le_clone_principal(depot: DepotRelecture) -> None:
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    assert "UI 3000 · API 8000" in depot.joue("--plan", "49").stdout


# =================================================================================================
# Ce qui est laissé derrière — rien, dans les trois cas
# =================================================================================================


def test_le_plan_necrit_rien_du_tout(depot: DepotRelecture) -> None:
    """`--plan` est la moitié gratuite du geste : il ne pose ni projet, ni dossier de captures.

    C'est ce qui autorise `/ticket-finish` à le jouer sur TOUS les tickets, y compris les neuf
    dixièmes qui n'ont pas d'écran.
    """
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    depot.joue("--plan", "50")
    assert not (depot.racine / ".maestro" / "relecture").exists()
    assert not (depot.racine / "core" / "projets").exists()


def test_la_preparation_monte_la_stack_en_demo_et_sans_navigateur(depot: DepotRelecture) -> None:
    """`--demo` pour des écrans PEUPLÉS — un poste vide ne montre pas le rendu qu'on vient
    d'écrire —, `--no-browser` parce que sans lui le script ouvre sa propre fenêtre et arrête la
    stack dès qu'elle se ferme (#149), coupant l'API sous le navigateur qu'on pilote."""
    depot.ports(8036, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue("51")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert depot.appels_start() == ["--demo --no-browser\tapi=8036\tui=3036"]
    assert (depot.racine / ".maestro" / "relecture" / "51").is_dir()
    assert (depot.racine / "core" / "projets" / "prj-demo.json").is_file()


def test_une_stack_qui_ne_demarre_pas_ne_laisse_rien_derriere(depot: DepotRelecture) -> None:
    """Le chemin d'échec est celui qui salit : le projet est posé AVANT le démarrage.

    Sans le retrait, un `--demo` raté laisserait un projet déclaré qui suivrait la session jusqu'au
    commit — et `core/projets/` est gitignoré, donc personne ne le verrait passer.
    """
    depot.ports(8036, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue("52", env={"MAESTRO_FAUX_START_CODE": "1"})
    assert resultat.returncode == 1
    assert not (depot.racine / "core" / "projets" / "prj-demo.json").exists()
    assert depot.appels_start()[-1].startswith("--stop"), "la stack est arrêtée avant qu'on parte"


def test_le_projet_pose_est_celui_du_scenario_de_demo(tmp_path: Path) -> None:
    """L'identifiant est LU dans `maestro/controltower/demo.py`, jamais recopié.

    Une constante recopiée des deux côtés d'une frontière est ce que #830 a vu casser : le jour où
    la démo change de projet, le script suit sans que personne ait à s'en souvenir.
    """
    depot = DepotRelecture(tmp_path / "depot", projet="prj-autre")
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    assert depot.joue("53").returncode == 0
    assert (depot.racine / "core" / "projets" / "prj-autre.json").is_file()


def test_fin_arrete_la_stack_et_retire_ce_quelle_a_pose(depot: DepotRelecture) -> None:
    """L'arrêt d'abord : un projet retiré sous une API vivante la laisserait servir un fantôme."""
    depot.ports(8036, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    depot.joue("54")
    resultat = depot.joue("--fin")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert depot.appels_start()[-1] == "--stop\tapi=8036\tui=3036"
    assert not (depot.racine / "core" / "projets" / "prj-demo.json").exists()


def test_fin_ne_retire_pas_un_projet_quon_na_pas_pose(depot: DepotRelecture) -> None:
    """Un projet déclaré avant nous ne nous appartient pas — c'est le témoin qui tranche, jamais le
    nom du fichier. Contre-exemple du test précédent : sans témoin, rien ne part."""
    depot.ports(8036, 3036)
    depot.ecris("core/projets/prj-demo.json", '{"id":"prj-demo"}\n')
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    depot.joue("55")
    depot.joue("--fin")
    assert (depot.racine / "core" / "projets" / "prj-demo.json").is_file()


# =================================================================================================
# Les refus d'usage — ils tombent avant tout le reste
# =================================================================================================


@pytest.mark.parametrize(
    ("args", "attendu"),
    [
        (("--plan",), "iid de ticket est attendu"),
        (("--plan", "couts"), "n'est pas un iid"),
        (("--fin", "42"), "ne prend pas d'iid"),
        (("--inconnue", "42"), "Option inconnue"),
    ],
)
def test_les_usages_fautifs_sont_refuses(
    depot: DepotRelecture, args: tuple[str, ...], attendu: str
) -> None:
    resultat = depot.joue(*args)
    assert resultat.returncode == 2, resultat.stdout + resultat.stderr
    assert attendu in resultat.stderr
    assert depot.appels_start() == []


# =================================================================================================
# Le verbe — `lib.sh relecture-note` (#935)
# =================================================================================================
# Le contenant serait pourtant celui d'`issue-note` : un commentaire sur le ticket, qui survit là
# où un résumé de session meurt avec sa console (#608, #795). Ce qui se garde ici est ce
# qu'`issue-note` ne porte PAS et qui EST le mécanisme — l'ancre, l'idempotence, et la
# distinction jouée / non jouée.
# Chacune est éprouvée SEULE : un test qui les vérifierait ensemble ne dirait pas laquelle garde.


def regle_relecture(iid: str, notes: tuple[str, ...] = (), existe: bool = True) -> dict:
    """Réponse à `gh_relecture_empreintes` — la lecture UNIQUE qui répond aux deux questions.

    « Ce ticket existe-t-il ? » et « cette relecture y est-elle déjà ? » tiennent dans un seul
    aller : le titre voyage avec les commentaires (forme de `gh_reste_source`, règle de #602). C'est
    ce qui permet au refus « iid inconnu » de tomber avant toute écriture sans rien coûter de plus.
    """
    issue = (
        None
        if not existe
        else {
            "title": "Un écran de plus",
            "comments": {"nodes": [{"body": corps} for corps in notes]},
        }
    )
    return {
        "contient": [f"issue(number:{iid})", "comments(first: 100)"],
        "reponse": {"data": {"repository": {"issue": issue}}},
    }


@pytest.fixture
def forge(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


def jugement(forge: Depot, nom: str, texte: str) -> str:
    """Écrit un jugement dans l'atelier de session et rend son chemin RELATIF.

    Relatif parce que c'est le régime réel : une session appelle ses commandes depuis son worktree,
    et tout chemin absolu lui est refusé (docs/10 §11.7).
    """
    chemin = forge.racine / ".maestro" / "session" / nom
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(texte, encoding="utf-8", newline="\n")
    return str(chemin.relative_to(forge.racine)).replace("\\", "/")


def notes_postees(forge: Depot, iid: str) -> list[str]:
    return [
        ligne
        for ligne in forge.appels()
        if "-X\tPOST" in ligne and f"issues/{iid}/comments" in ligne
    ]


def corps_poste(forge: Depot, iid: str) -> str:
    """Le texte réellement envoyé. Il voyage par `-F body=@<fichier>` — jamais sur la ligne de
    commande (#233) — et le double résout le fichier, sauts de ligne échappés."""
    return notes_postees(forge, iid)[-1].split("\tbody=", 1)[1].replace("\\n", "\n")


def test_le_jugement_est_consigne_sous_une_ancre_reconnaissable(forge: Depot) -> None:
    """Sans en-tête reconnaissable, « ce ticket a-t-il été relu ? » n'a pas de réponse — or c'est
    la question du critère. L'empreinte y est en clair : c'est elle que le tour suivant relira."""
    forge.pose_etat(graphql=[regle_relecture("60")])
    fichier = jugement(forge, "vu.md", "Les deux thèmes vus, rien à signaler.\n")
    acheve = forge.lib("relecture-note", "60", fichier)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    corps = corps_poste(forge, "60")
    assert corps.startswith("## Relecture visuelle — empreinte ")
    assert "Les deux thèmes vus, rien à signaler." in corps


def test_rejoue_a_lidentique_il_necrit_rien(forge: Depot) -> None:
    """`/ticket-finish` se rejoue — pipeline rouge, deux passes `/mr-fix`, reprise d'un run.

    L'empreinte `cksum` est celle de `reste-claude` et de `veille-differe`, et son contrat est le
    même : rejeu à l'identique MUET. Sans lui, une clôture qui repasse trois fois empilerait trois
    fois le même jugement sur le ticket.
    """
    fichier = jugement(forge, "vu.md", "Rien à signaler.\n")
    forge.pose_etat(graphql=[regle_relecture("61")])
    forge.lib("relecture-note", "61", fichier)
    empreinte = corps_poste(forge, "61").split("empreinte ")[1].split("\n")[0].strip()

    forge.pose_etat(graphql=[regle_relecture("61", notes=(f"## X — empreinte {empreinte}",))])
    avant = len(notes_postees(forge, "61"))
    acheve = forge.lib("relecture-note", "61", fichier)
    assert acheve.returncode == 0
    assert "déjà" in acheve.stdout
    assert len(notes_postees(forge, "61")) == avant, "aucun second commentaire"


def test_un_jugement_enrichi_sajoute_au_lieu_decraser(forge: Depot) -> None:
    """Deux relectures d'un même ticket sont deux constats, pas une correction de l'autre — le cas
    d'un écran retouché après un premier passage. Contre-exemple du test précédent."""
    forge.pose_etat(
        graphql=[regle_relecture("62", notes=("## Relecture visuelle — empreinte 111-22",))]
    )
    fichier = jugement(forge, "vu.md", "Deuxième passage : le contraste est corrigé.\n")
    acheve = forge.lib("relecture-note", "62", fichier)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert len(notes_postees(forge, "62")) == 1
    assert "le contraste est corrigé" in corps_poste(forge, "62")


def test_non_jouee_ne_ressemble_pas_a_regarde_rien_a_signaler(forge: Depot) -> None:
    """Le défaut même qu'on corrige, et il est porté par le VERBE, jamais par la prose du fichier.

    `--raison` le dit dans le TITRE de la section — là où on le lit sans dérouler. Deux textes
    peuvent se ressembler ; deux en-têtes, non.
    """
    forge.pose_etat(graphql=[regle_relecture("63")])
    fichier = jugement(forge, "pourquoi.md", "La stack n'a pas démarré : port occupé.\n")
    acheve = forge.lib("relecture-note", "--raison", "63", fichier)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    corps = corps_poste(forge, "63")
    assert corps.startswith("## Relecture visuelle — NON JOUÉE — empreinte ")
    assert "n'a **pas** été regardé" in corps
    assert "NON JOUÉE" in acheve.stdout


@pytest.mark.parametrize("options", [(), ("--raison",)])
def test_le_fichier_est_obligatoire_dans_les_deux_sens(
    forge: Depot, options: tuple[str, ...]
) -> None:
    """La moitié la plus facile à défaire. Côté jugement, ce que la session a d'irremplaçable est ce
    qu'elle a VU ; côté `--raison`, une absence sans motif est exactement ce qu'on ne veut plus
    pouvoir écrire — un `--raison` sans fichier rendrait le mécanisme contournable en un mot.
    """
    forge.pose_etat(graphql=[regle_relecture("64")])
    acheve = forge.lib("relecture-note", *options, "64", ".maestro/session/absent.md")
    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert ecritures(forge) == [], "un refus ne laisse rien derrière lui"


def test_un_fichier_vide_est_refuse_lui_aussi(forge: Depot) -> None:
    """Un commentaire vide ne dit pas si l'écran a été regardé — c'est-à-dire la question posée."""
    forge.pose_etat(graphql=[regle_relecture("65")])
    acheve = forge.lib("relecture-note", "65", jugement(forge, "vide.md", ""))
    assert acheve.returncode == 4
    assert ecritures(forge) == []


def test_le_refus_gratuit_tombe_avant_toute_lecture_de_forge(forge: Depot) -> None:
    """Règle de `gl_reste_claude` : le contrôle qui ne coûte rien passe en premier.

    Le fichier manquant est jugé AVANT le premier aller — un refus ne coûte donc ni un appel, ni le
    risque d'une écriture partielle.
    """
    forge.pose_etat(graphql=[regle_relecture("66")])
    forge.lib("relecture-note", "66", ".maestro/session/absent.md")
    assert forge.appels() == [], "pas même une lecture"


def test_un_ticket_inconnu_est_refuse_sans_rien_ecrire(forge: Depot) -> None:
    forge.pose_etat(graphql=[regle_relecture("67", existe=False)])
    acheve = forge.lib("relecture-note", "67", jugement(forge, "vu.md", "Vu.\n"))
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "introuvable" in acheve.stderr
    assert ecritures(forge) == []


def test_un_iid_qui_nen_est_pas_un_est_refuse_sans_rien_ecrire(forge: Depot) -> None:
    acheve = forge.lib("relecture-note", "chat", jugement(forge, "vu.md", "Vu.\n"))
    assert acheve.returncode == 3
    assert forge.appels() == []


def test_une_forge_muette_ne_fait_pas_semblant_davoir_consigne(forge: Depot) -> None:
    """Un `1` **ne bloque pas la clôture** (le prompt le dit), mais il ne doit pas se faire passer
    pour un succès : ce que le dispositif rend difficile est l'absence de TRACE, jamais le merge."""
    forge.pose_etat(graphql=[{"contient": ["issue(number:68)"], "reponse": {"data": None}}])
    acheve = forge.lib("relecture-note", "68", jugement(forge, "vu.md", "Vu.\n"))
    assert acheve.returncode == 1
    assert ecritures(forge) == []


def test_la_consignation_ne_coute_quun_aller_de_lecture(forge: Depot) -> None:
    """Ce qui se garde est le NOMBRE d'allers, jamais une durée (règle de #577, #602).

    Un chronomètre en CI mesure la charge de la machine ; le compte, lui, porte la décision — le
    titre voyage avec les commentaires précisément pour que la question du ticket inconnu ne coûte
    pas un second appel.
    """
    forge.pose_etat(graphql=[regle_relecture("69")])
    forge.lib("relecture-note", "69", jugement(forge, "vu.md", "Vu.\n"))
    lectures = [ligne for ligne in forge.appels() if ligne.startswith("api\tgraphql")]
    assert len(lectures) == 1, lectures


def test_sans_argument_le_verbe_rend_son_usage(forge: Depot) -> None:
    acheve = forge.lib("relecture-note")
    assert acheve.returncode == 2
    assert "usage" in acheve.stderr


# =================================================================================================
# Le déclencheur — ce qui appelle le geste, et ce qu'il ne recopie pas
# =================================================================================================
# Le lot 2 a rendu la relecture jouable ; elle est restée **appelée par rien**, exactement comme
# `/design-veille` entre #708 et #714. Un geste que personne n'appelle est une règle lue, pas un
# mécanisme — « une checklist qu'aucune machine ne vérifie ne tient pas » (docs/30 §3.6).


def test_la_cloture_nomme_le_plan_et_le_verbe() -> None:
    """Les deux moitiés : la question (`--plan`) et la trace (`relecture-note`).

    Sans la première l'étape n'a pas de déclencheur ; sans la seconde le jugement meurt avec la
    console de la session, ce qui est le défaut que #608 et #795 ont déjà nommé deux fois.
    """
    texte = PROMPT_FINISH.read_text(encoding="utf-8")
    assert "relecture-visuelle.sh --plan" in texte
    assert "lib.sh relecture-note" in texte
    assert "--raison" in texte, "l'absence porte une raison ENREGISTRÉE, pas un silence"


def etape_4bis() -> str:
    """L'étape 4bis de `/ticket-finish`, espaces normalisés — le prompt est replié à 100
    colonnes, donc une phrase y est coupée n'importe où et ne se cherche pas telle quelle.

    La **borne** compte, sans suffire : plusieurs des mots gardés par les tests ci-dessous
    (« muet », « raison ») se disent **aussi dans cette étape** pour autre chose, ce dont le
    premier d'entre eux tire la conséquence.
    """
    texte = PROMPT_FINISH.read_text(encoding="utf-8")
    debut = texte.index("4bis. **Le rendu a-t-il été regardé ?**")
    return " ".join(texte[debut : texte.index("5. **Filet CI local**")].split())


def test_la_cloture_est_muette_quand_il_ny_a_rien_a_regarder() -> None:
    """Le `3` est ce qui rend l'étape tenable sur tout le reste du backlog, et son prix est le
    **silence**. La grande majorité des tickets ne touchent aucun écran : une étape qui
    s'annoncerait quand même poserait une ligne de bruit sur chacun d'eux, jusqu'à ce qu'on
    cesse de la lire — le défaut que #562 nomme pour écarter un signalement qui parle partout,
    et la règle déjà tenue par `gc --auto` et par l'abstention de `demarre-parent`.

    ⚠ **Borner à l'étape 4bis ne suffit pas ici**, et l'échantillon fautif l'a montré :
    « muette » se dit **deux fois de plus dans cette même étape** — l'idempotence de
    `relecture-note`, et la « forge muette » qui ne bloque pas la clôture —, si bien qu'un
    `in etape` sur ce seul mot reste vert quand on rend l'abstention bavarde. L'ancre est donc
    la **phrase entière** que seule la branche `3` porte.
    """
    etape = etape_4bis()
    assert "code `3`" in etape, (
        "motif creux : l'étape ne distingue plus les codes du plan, le contrôle ci-dessous ne "
        "prouverait plus rien"
    )
    assert "ne le mentionne pas" in etape, (
        "le silence porte sur le RÉSUMÉ, pas seulement sur le verbe"
    )
    assert "n'appelle aucun verbe" in etape, (
        "une abstention qui consignerait quand même « aucun écran » écrirait sur le ticket une "
        "information que `--plan` rend en deux secondes à qui la demande"
    )
    bavard = etape.replace("L'abstention nominale est muette", "L'abstention est annoncée")
    assert "muette" in bavard, (
        "c'est le piège que l'ancre suivante évite, et il est réel : sur une étape rendue bavarde, "
        "« muette » reste vrai grâce à la « forge muette » de la même section"
    )
    assert "L'abstention nominale est muette" in etape, (
        "c'est la phrase de la branche `3` qu'on garde, jamais le mot seul"
    )


def test_la_cloture_joue_au_lieu_de_demander_en_run_comme_en_interactif() -> None:
    """La différence avec l'étape 5 de `/ticket-start`, qui **propose** : une veille est un
    jugement sur l'**opportunité de chercher** des références, là où regarder l'écran qu'on
    vient d'écrire est un **constat**. C'est ce qui rend l'étape jouable là où il n'y a personne
    à qui demander, donc ce qu'une relecture du prompt risque le plus de « corriger » en
    alignant les deux étapes l'une sur l'autre — et l'aligner coûterait cher dans ce sens-là :
    en run, une question sans répondant ne fait pas attendre, elle fait **passer** (#788), donc
    plus aucun écran ne serait regardé.
    """
    etape = etape_4bis()
    assert "On ne demande pas, on joue" in etape
    assert "à l'identique en run et en interactif" in etape, (
        "sans cette phrase, le régime des deux appelants redevient une question ouverte, et c'est "
        "la session de run qui la tranchera dans le sens du silence"
    )


def test_la_cloture_regarde_avant_de_jouer_le_filet_ci() -> None:
    """L'ordre EST le contenu de la décision, et c'est celui de `/mr-fix` : ce qui peut **changer le
    diff** passe avant le verdict qui le juge.

    Un contraste qui saute en thème sombre se corrige, et le filet CI joue ensuite une fois — pas
    deux. Le test lit l'ordre des étapes dans le prompt, seul endroit où il est porté.
    """
    texte = PROMPT_FINISH.read_text(encoding="utf-8")
    relecture = texte.index("4bis. **Le rendu a-t-il été regardé ?**")
    filet = texte.index("5. **Filet CI local**")
    assert relecture < filet, (
        "la relecture doit passer AVANT le filet CI : un correctif de rendu change le diff, et le "
        "filet jugerait alors un état qui n'existe plus (docs/30 §5.5)"
    )


def test_la_cloture_delegue_la_sequence_au_skill_au_lieu_de_la_recopier() -> None:
    """Règle de #310, gardée ailleurs pour la recette CI : une séquence recopiée dans un prompt fige
    l'outil au jour où elle a été écrite, et c'est le prompt que la session lit en dernier.

    Le contrôle porte sur les gestes de navigateur : les nommer ici serait la recopie. Le motif est
    prouvé sur un échantillon fautif avant de conclure de son absence.
    """
    texte = PROMPT_FINISH.read_text(encoding="utf-8")
    assert "skill `relecture-visuelle`" in texte, "la source unique est nommée"
    fautif = texte + "\nmcp__chrome-maestro__browser_navigate vers http://localhost:3000/couts\n"
    assert "browser_navigate" in fautif, "motif creux : le contrôle ci-dessous ne prouverait rien"
    assert "browser_navigate" not in texte, (
        "la séquence du navigateur appartient au skill, pas au prompt (#310)"
    )


def test_le_ship_herite_sans_seconde_implementation() -> None:
    """`/ticket-ship` délègue tout à `/ticket-finish` depuis toujours — comme pour le ramassage de
    worktree de #519, elle hérite **sans une ligne à elle**.

    Ce qu'on garde est donc l'inverse de d'habitude : qu'elle ANNONCE la relecture (une clôture
    qui prend une minute de plus sur un ticket d'interface doit le dire) et qu'elle ne la **rejoue**
    pas, une seconde implémentation étant le premier moyen que les deux divergent.
    """
    texte = PROMPT_SHIP.read_text(encoding="utf-8")
    assert "relecture visuelle" in texte
    assert "relecture-visuelle.sh" not in texte
    assert "relecture-note" not in texte


def test_le_skill_commence_par_le_plan() -> None:
    """Le skill est la source unique de la séquence, et sa première étape est celle qui ne coûte
    rien : demander s'il y a matière. Deux des trois réponses terminent le geste."""
    texte = SKILL.read_text(encoding="utf-8")
    assert "name: relecture-visuelle" in texte
    assert "relecture-visuelle.sh --plan" in texte
    assert texte.index("--plan") < texte.index("--fin"), "on demande avant de monter la stack"


def test_le_geste_est_joignable_des_deux_cotes() -> None:
    """L'`allow` d'un run est l'UNION des deux fichiers (docs/10 §11.7), mais les deux listes ne
    décrivent pas le même objet : en interactif elle retire de la **friction**, en run elle définit
    la **frontière** — ce qui n'y est pas n'existe pas, faute de répondant (#788).

    D'où la portée de ce contrôle : le verbe des deux côtés, et `start.sh` **au moins** du côté du
    run — c'est le blocage réel que le parent #930 avait nommé (le navigateur, lui, passait déjà).
    Une session interactive qui monte une stack a quelqu'un pour approuver ; une session de run n'a
    personne, et sans cette règle elle n'a aucune stack à regarder.
    """
    for chemin in (REGLAGES_RUN, REGLAGES_DEPOT):
        allow = json.loads(chemin.read_text(encoding="utf-8"))["permissions"]["allow"]
        assert any(r.startswith("Bash(") for r in allow), "allowlist vide ou mal lue : test creux"
        assert "Bash(bash scripts/design/relecture-visuelle.sh:*)" in allow, (
            f"{chemin.name} ne laisse pas jouer la relecture visuelle : le geste redeviendrait "
            "une règle lue que rien n'exécute (#932)"
        )
    run = json.loads(REGLAGES_RUN.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert "Bash(bash scripts/controltower/start.sh:*)" in run, (
        "sans la stack, une session de run n'a rien à regarder — c'est le blocage que #930 avait "
        "mesuré, et il tient en une règle"
    )


def test_ce_qui_souvre_ici_ne_deborde_pas_sur_le_reste_du_run() -> None:
    """⚠ Ce chantier ouvre la **stack** et le **web**, jamais la parité — et la nuance se perd vite.

    Le contrôle porte sur ce que les deux règles de #932 autorisent : deux processus **locaux** sur
    les ports du worktree, et un verbe qui ne parle à aucune forge. Une règle qui ouvrirait
    `bash scripts/gitlab/lib.sh` en grand, ou `bash` nu, emporterait avec elle bien autre chose —
    à commencer par les deux verbes de G5, dont le refus n'est pas un trou mais un **interdit
    voulu** (#788 G5, #419 ; gardé en entier, règle · rapport · doc, par `tests/test_ecart_run.py`,
    qui est aussi l'endroit où l'on vérifiera qu'il n'a pas été « corrigé » par erreur).
    """
    run = json.loads(REGLAGES_RUN.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert "Bash(bash:*)" not in run, (
        "une règle `bash` nue ferait sauter la borne des règles `Bash(bash scripts/…)`, qui limite "
        "l'interpréteur aux scripts versionnés — et rouvrirait du même coup ce que G5 refuse"
    )
    deny = json.loads(REGLAGES_RUN.read_text(encoding="utf-8"))["permissions"]["deny"]
    assert any("merge-mr" in regle for regle in deny), (
        "le `deny` de G5 a disparu en même temps qu'on ouvrait la relecture : les deux n'ont rien "
        "à voir — voir tests/test_ecart_run.py, qui en porte la garde complète"
    )
