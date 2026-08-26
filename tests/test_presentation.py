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
  place.

⚠ **Où chaque section peut répondre.** Les tests de la dérivation et du rendu tournent partout.
Ceux du tournage exigent `node` : présent sur les postes (`.tools/node`, §Environnement Node) et
sur le runner `ubuntu-latest` du job `pytest`, **absent de l'image `python:3.11`** du filet CI
local (#372) — ils y seront donc SAUTÉS, et le verdict qui les joue est celui de la pipeline. Un
saut qui passerait inaperçu **en CI** est exactement le défaut de #333 : c'est pourquoi
`test_node_ne_manque_pas_en_ci` en fait une erreur franche plutôt qu'un `s` de plus dans le compte
rendu.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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

    def ecrans(self, *iids: object, check: bool = False) -> subprocess.CompletedProcess[str]:
        args = ["scripts/presentation/ecrans-touches.sh"]
        if check:
            args.append("--check")
        args += [str(i) for i in iids]
        return subprocess.run(
            [BASH, *args],
            cwd=self.racine,
            capture_output=True,
            text=True,
            encoding="utf-8",
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
def test_la_plomberie_de_l_ui_est_hors_perimetre(depot: DepotEcrans) -> None:
    """`lib/` et `hooks/` sont exclus À DESSEIN : presque tous les tickets de la Control Tower y
    touchent, et les compter ferait rendre une ligne indéterminée à presque tous."""
    depot.commit(
        "feat: plomberie\n\nCloses #17",
        ["apps/web/lib/navigation.ts", "apps/web/hooks/useControlTower.ts"],
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
#:   FAUX_PAGE_NON_PRETE_VIDEO=<n>  la page du n-ième clip n'atteint jamais son marqueur → échec
#:                                  DOUX : le clip est conservé, écourté, `complet: false`.
#:   FAUX_ECHEC_CAPTURES=1          toutes les captures échouent → c'est le seul cas qui doit
#:                                  changer le code de retour.
FAUX_PLAYWRIGHT = """\
"use strict";
// Faux playwright-core — écrit par tests/test_presentation.py (#547). Aucun navigateur.
const { mkdirSync, writeFileSync } = require("node:fs");
const { dirname } = require("node:path");

const echecContexte = Number(process.env.FAUX_ECHEC_CONTEXTE_VIDEO || 0);
const pageNonPrete = Number(process.env.FAUX_PAGE_NON_PRETE_VIDEO || 0);
const echecCaptures = process.env.FAUX_ECHEC_CAPTURES === "1";
let contextesVideo = 0;

function ecrire(chemin, contenu) {
  mkdirSync(dirname(chemin), { recursive: true });
  writeFileSync(chemin, contenu);
}

function faussePage(rangVideo) {
  const locator = {
    first: () => locator,
    filter: () => locator,
    async count() { return 1; },
    async click() {},
    async waitFor() {},
  };
  return {
    async goto() {
      if (!rangVideo && echecCaptures) throw new Error("route injoignable (faux)");
    },
    async waitForFunction() {
      if (rangVideo && rangVideo === pageNonPrete) throw new Error("marqueur absent (faux)");
    },
    async waitForTimeout() {},
    async evaluate() {},
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
          return {
            async addInitScript() {},
            async newPage() { return faussePage(rangVideo); },
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
    """Joue `captures.mjs` contre le stub et rend (processus, manifeste)."""
    environnement = dict(os.environ)
    environnement["MAESTRO_PLAYWRIGHT_HOME"] = str(maison)
    environnement.update(commutateurs)
    appel = [NODE, str(CAPTURES_MJS), "--sortie", str(sortie), "--base", "http://127.0.0.1:9"]
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

    # Les autres continuent : c'est la seconde moitié de la règle.
    autres = clips[:1] + clips[2:]
    assert autres and all(c["fichier"] and c["erreur"] is None for c in autres)

    # …et les captures ne s'en aperçoivent pas. Le code de retour ne dépend QUE d'elles.
    assert processus.returncode == 0, processus.stderr
    assert all(page["fichier"] for page in manifeste["pages"])


@besoin_de_node
def test_une_page_non_prete_garde_son_clip_ecourte(
    tmp_path: Path, maison_playwright: Path
) -> None:
    """L'autre régime d'échec, et il ne se confond pas avec le précédent : la page n'atteint pas
    son marqueur, les gestes sont abandonnés — mais le clip EXISTE et montre ce que la page a su
    rendre. `complet: false` laisse l'appelant décider de le retenir."""
    sortie = tmp_path / "captures"
    _, manifeste = tourner(sortie, maison_playwright, FAUX_PAGE_NON_PRETE_VIDEO="1")

    ecourte = manifeste["videos"][0]
    assert ecourte["fichier"], "un clip écourté se conserve — il montre ce qu'il a pu montrer"
    assert (sortie / ecourte["fichier"]).exists()
    assert ecourte["complet"] is False
    assert ecourte["erreur"].startswith("page non prête")
    assert all(c["complet"] for c in manifeste["videos"][1:])


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

    def construire(self, donnees: dict, **variables: str) -> subprocess.CompletedProcess[str]:
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
            [sys.executable, str(BUILD_PY), str(fichier), "--sortie", str(self.sortie)],
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

#: La phrase de l'étape 5 d'avant #543, celle qui envoyait poser une clé de capture au jugé. Sa
#: survivance serait une contradiction dans le même fichier : un prompt est ce que la session lit
#: en dernier, et deux consignes opposées se tranchent par la dernière lue (leçon de #310).
DEVINETTE_RETIREE = "quand la page illustre vraiment le ticket"


def test_les_motifs_de_doc_attrapent_un_echantillon_fautif() -> None:
    """Prouver les motifs sur des textes fabriqués avant de balayer les vrais fichiers.

    Sans cette moitié, les quatre tests suivants rendraient un ✓ sur une question jamais posée —
    c'est la méthode déjà employée par `tests/test_ci_local.py` et `tests/test_cycle_de_vie.py`.
    """
    for famille in (MOTIFS_DERIVATION, MOTIFS_PARCOURS, MOTIFS_RESUME, MOTIFS_LIMITES):
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
    manque = "Captures indisponibles : la stack de démo n'a pas démarré."
    processus = presentation.construire(donnees_minimales(notes=[manque]))
    assert processus.returncode == 0, processus.stderr

    pied = pied_de(presentation.html())
    assert "<ul>" in pied
    assert "la stack de démo n" in pied and "a pas démarré" in pied


def test_la_commande_borne_ce_que_les_notes_acceptent() -> None:
    """Le gabarit ne peut pas distinguer une réserve de production d'un échec de génération : c'est
    le prompt qui tranche, et c'est donc lui qu'on garde."""
    morceaux = COMMANDE.read_text(encoding="utf-8").split("`notes` ne porte que", 1)
    assert len(morceaux) == 2, "/milestone-presentation ne dit plus ce que `notes` accepte"
    regle = morceaux[1].split("\n\n", 1)[0]
    assert "Ce que la commande ne sait pas" in regle, (
        "la règle ne renvoie pas les limites méthodologiques vers le résumé du terminal"
    )
