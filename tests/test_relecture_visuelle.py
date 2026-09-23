"""Tests de la relecture visuelle — le geste qui regarde, et ce qui l'appelle (#932, #935, #972).

La chaîne visuelle de [docs/30 §5.1](../docs/30-cible-visuelle-control-tower.md) sait **viser**
(`/design-veille`), **tenir** (tokens, primitives) et **garder** (contraste, a11y, sobriété,
géométrie). Personne n'y **regardait** : une session pouvait écrire une interface, voir tous ses
tests verts, et n'avoir jamais ouvert l'écran qu'elle venait de changer. Le lot 2 (#932) a rendu le
geste jouable (docs/30 §5.6), le lot 5 (#935) l'a rendu **obligatoire à la clôture** (§5.5).

Le chantier #972 (docs/30 §5.8) a ensuite donné au regard **une référence** : il vérifiait qu'un
écran est conforme, pas qu'il est **voulu**. Ce module en garde quatre des cinq lots — le lot 4 (les
variantes, #979) vit dans [`test_design_veille.py`](test_design_veille.py), à côté du critère
« décide ou applique » qu'il partage avec la veille :

* **l'attente** (#976) — la section « Rendu attendu » des gabarits, ce que `/ticket-create` en fait,
  ce que le brief de `/ticket-start` en rend ; et sa lecture par `lib.sh relecture-attente` (#980) ;
* **l'avant** (#977) — `origin/main` servi à côté de la branche, jamais à sa place, et best-effort ;
* **les états** (#978, sur la vraie stack depuis #1165) — montés par `--etat`, comptés par
  `--couverture`, et ceux que la vraie stack ne produit pas **nommés non couverts**, jamais imités.
  Les gestes du lanceur qui les servent (`--etat-banc`, `--etat-neuf`, `--couper-api`) sont gardés
  dans [`test_controltower_mode_reel.py`](test_controltower_mode_reel.py), l'état du banc dans
  [`test_etat_banc.py`](test_etat_banc.py) ;
* **le regard neuf** (#980) — la grille que `relecture-note` refuse de voir amputée, la saisine qui
  ne porte que les pièces, la planche qui survit au worktree, et le sous-agent qui n'a que `Read`.

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
Ce que le script demande à l'API elle-même — les projets servis, la déclaration du projet neuf,
l'espace qu'elle sert — l'est à une fausse API **sur un vrai port HTTP** (`FausseApi`) : la
conversation se joue pour de vrai, seules les réponses sont écrites d'avance.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from harnais_forge import BASH, GIT, RACINE, Depot, corps_ticket, ecritures, monte_depot

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

RELECTURE_SH = RACINE / "scripts" / "design" / "relecture-visuelle.sh"
ECRANS_TOUCHES = RACINE / "scripts" / "presentation" / "ecrans-touches.sh"
SKILL = RACINE / ".claude" / "skills" / "relecture-visuelle" / "SKILL.md"
PROMPT_FINISH = RACINE / ".claude" / "commands" / "ticket-finish.md"
PROMPT_SHIP = RACINE / ".claude" / "commands" / "ticket-ship.md"
PROMPT_CREATE = RACINE / ".claude" / "commands" / "ticket-create.md"
PROMPT_START = RACINE / ".claude" / "commands" / "ticket-start.md"
PROMPT_VEILLE = RACINE / ".claude" / "commands" / "design-veille.md"
AGENT_REGARD = RACINE / ".claude" / "agents" / "regard-neuf.md"
GRILLE = RACINE / "scripts" / "design" / "grille-relecture.tsv"
PLANCHE_PY = RACINE / "scripts" / "design" / "planche.py"
BUILD_PY = RACINE / "scripts" / "presentation" / "build.py"
LIB_SH = RACINE / "scripts" / "gitlab" / "lib.sh"
RELECTURE_PROJETS = RACINE / "scripts" / "design" / "relecture-projets.py"
GABARITS = RACINE / ".github" / "ISSUE_TEMPLATE"
REGLAGES_RUN = RACINE / "scripts" / "orchestrate" / "settings.run.json"
REGLAGES_DEPOT = RACINE / ".claude" / "settings.json"


def etats_du_script() -> list[str]:
    """Les états que le script déclare — LUS dans le script, pour que le skill et ces tests suivent
    le jour où il en change."""
    trouve = re.search(r'^ETATS="([^"]+)"', RELECTURE_SH.read_text(encoding="utf-8"), re.M)
    assert trouve, "ETATS introuvable dans relecture-visuelle.sh"
    return trouve.group(1).split()


#: Un PNG de quelques octets : ni la planche ni le script ne le décodent, ils le COMPTENT et
#: l'encodent — la même économie que les fixtures de `test_presentation.py`.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120


def faux_start(refuse: str = "") -> str:
    """Le double de `start.sh` : il journalise la commande ET les ports reçus — les deux sont la
    décision à garder —, et son code de retour se pilote pour jouer la stack qui ne démarre pas.
    `refuse` lui fait refuser une option, comme un lanceur plus ancien qui ne la connaît pas (code
    `2`, avant de rien journaliser — ce que fait le vrai)."""
    return f"""#!/usr/bin/env bash
racine="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/../.." && pwd)"
refuse="{refuse}"
for option in "$@"; do
  if [ -n "$refuse" ] && [ "$option" = "$refuse" ]; then
    echo "Option inconnue : $option" >&2; exit 2
  fi
done
mkdir -p "$racine/.maestro"
printf '%s\\tapi=%s\\tui=%s\\n' "$*" "${{MAESTRO_PORT_API:-}}" "${{MAESTRO_PORT_UI:-}}" \\
  >>"$racine/.maestro/start.log"
exit "${{MAESTRO_FAUX_START_CODE:-0}}"
"""


FAUX_START = faux_start()


def ports_libres() -> int:
    """Un port d'API libre ET son décalé de 200 (celui de l'avant) : deux fausses API peuvent y
    répondre."""
    for _ in range(200):
        with socket.socket() as sonde:
            sonde.bind(("127.0.0.1", 0))
            port = int(sonde.getsockname()[1])
        if port + 200 > 65535:
            continue
        with socket.socket() as sonde:
            try:
                sonde.bind(("127.0.0.1", port + 200))
            except OSError:
                continue
        return port
    raise RuntimeError("aucune paire de ports libres")


class FausseApi:
    """L'API réelle réduite à ce que la relecture lui demande, sur un vrai port HTTP.

    `GET /api/sante` rend l'espace qu'elle sert (celui du banc ou d'une copie), `GET /api/projets`
    et `GET /api/executions?projet=<id>` les projets et leurs runs, `POST /api/projets` déclare un
    projet (ou le refuse en 422, comme la validation des racines). Tout ce qu'elle reçoit est
    gardé dans `recus`.
    """

    def __init__(
        self,
        port: int,
        *,
        espace: str = "1165-copie",
        projets: tuple[dict[str, Any], ...] = (),
        runs: dict[str, int] | None = None,
        refus: str = "",
    ) -> None:
        self.espace = espace
        self.projets = [dict(projet) for projet in projets]
        self.runs = runs or {}
        self.refus = refus
        self.recus: list[tuple[str, str, Any]] = []
        api = self

        class Guichet(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # silence : pytest n'a pas à le lire
                pass

            def _rendre(self, statut: int, corps: Any) -> None:
                donnees = json.dumps(corps).encode("utf-8")
                self.send_response(statut)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(donnees)))
                self.end_headers()
                self.wfile.write(donnees)

            def do_GET(self) -> None:  # noqa: N802 - nom imposé par http.server
                url = urlparse(self.path)
                api.recus.append(("GET", url.path, parse_qs(url.query)))
                if url.path == "/api/sante":
                    self._rendre(200, {"statut": "ok", "espace": api.espace})
                elif url.path == "/api/projets":
                    self._rendre(200, api.projets)
                elif url.path == "/api/executions":
                    projet = parse_qs(url.query).get("projet", [""])[0]
                    self._rendre(200, [{"run_id": f"r{i}"} for i in range(api.runs.get(projet, 0))])
                else:
                    self._rendre(404, {"detail": "inconnu"})

            def do_POST(self) -> None:  # noqa: N802 - nom imposé par http.server
                longueur = int(self.headers.get("Content-Length") or 0)
                corps = json.loads(self.rfile.read(longueur).decode("utf-8") or "{}")
                api.recus.append(("POST", self.path, corps))
                if api.refus:
                    self._rendre(422, {"detail": {"motif": "refus", "message": api.refus}})
                    return
                projet = {"id": f"prj-neuf{len(api.projets) + 1}", **corps}
                api.projets.append(projet)
                self._rendre(201, projet)

        self._serveur = ThreadingHTTPServer(("127.0.0.1", port), Guichet)
        self._fil = threading.Thread(target=self._serveur.serve_forever, daemon=True)

    def __enter__(self) -> FausseApi:
        self._fil.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._serveur.shutdown()
        self._serveur.server_close()

    def declarations(self) -> list[dict[str, Any]]:
        return [corps for methode, chemin, corps in self.recus if methode == "POST"]


class DepotRelecture:
    """Un dépôt jetable où `relecture-visuelle.sh` se croit chez lui.

    Le script résout sa racine par `$(dirname $BASH_SOURCE)/../..` et tout le reste en dérive —
    `ecrans-touches.sh`, `relecture-projets.py`, `.claude/settings.local.json`, `apps/web/`. Le
    recopier sous `scripts/design/` du dépôt d'essai suffit donc à le faire travailler là, sans
    rien injecter : c'est la même mécanique qu'en production.
    """

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        racine.mkdir(parents=True)
        self._git("init", "-b", "main")
        # Identité LOCALE : l'image du job pytest n'en pose aucune globalement, et c'est délibéré
        # (#333 — une identité globale remasquerait le bug qu'elle a fait sortir).
        self._git("config", "user.email", "essai@maestro.test")
        self._git("config", "user.name", "Essai Maestro")

        for source, relatif in (
            (RELECTURE_SH, "scripts/design/relecture-visuelle.sh"),
            (RELECTURE_PROJETS, "scripts/design/relecture-projets.py"),
            (ECRANS_TOUCHES, "scripts/presentation/ecrans-touches.sh"),
        ):
            cible = racine / relatif
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, cible)

        self.ecris("scripts/controltower/start.sh", FAUX_START)
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

    def origin_main(self) -> None:
        """Fige `origin/main` sur HEAD : l'avant (#977) sans remote, dont le script lit la ref."""
        self._git("update-ref", "refs/remotes/origin/main", "HEAD")

    def recopie(self, *sources: Path) -> None:
        """Recopie des fichiers du dépôt à leur place dans le décor — ceux que le script LIT."""
        for source in sources:
            cible = self.racine / source.relative_to(RACINE)
            cible.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, cible)

    def capture(self, relatif: str, contenu: bytes = PNG) -> None:
        fichier = self.racine / relatif
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(contenu)

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
        self, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        # Le poste ne décide de rien : les deux ports qu'une session porterait sont retirés, pour
        # que ce que le test observe vienne du décor et non de la machine (règle du conftest).
        # L'avant non plus : un poste qui l'aurait éteint rendrait muets les tests qui le regardent.
        for cle in ("MAESTRO_PORT_API", "MAESTRO_PORT_UI", "MAESTRO_RELECTURE_AVANT"):
            environnement.pop(cle, None)
        # Et le projet neuf de l'état « vide » naît à côté du décor, jamais dans le profil du poste.
        environnement["MAESTRO_RELECTURE_ATELIER"] = str(self.atelier)
        environnement.update(env or {})
        assert BASH is not None
        return subprocess.run(  # noqa: S603
            [BASH, "scripts/design/relecture-visuelle.sh", *args],
            cwd=cwd or self.racine,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environnement,
        )

    def appels_start(self, racine: Path | None = None) -> list[str]:
        journal = (racine or self.racine) / ".maestro" / "start.log"
        if not journal.exists():
            return []
        return [ligne for ligne in journal.read_text(encoding="utf-8").splitlines() if ligne]

    @property
    def atelier(self) -> Path:
        """L'atelier des relectures du décor (`MAESTRO_RELECTURE_ATELIER`)."""
        return self.racine.parent / "atelier-relecture"

    def temoin(self, nom: str) -> Path:
        return self.racine / ".maestro" / "relecture" / nom


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


def test_la_preparation_monte_la_vraie_stack_sur_l_etat_du_banc_sans_navigateur(
    depot: DepotRelecture,
) -> None:
    """L'état peuplé est celui qu'un vrai passage du banc a laissé (`--etat-banc`, #1164) — plus
    la démo (#1165). `--no-browser` parce que sans lui le lanceur ouvre sa propre fenêtre et arrête
    la stack dès qu'elle se ferme (#149), coupant l'API sous le navigateur qu'on pilote. Et rien
    n'est écrit dans les données de la copie : ni `core/projets/`, ni projet posé à la main."""
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue("51")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert depot.appels_start() == [f"--etat-banc --no-browser\tapi={api}\tui=3036"]
    assert all("--demo" not in appel for appel in depot.appels_start())
    assert (depot.racine / ".maestro" / "relecture" / "51").is_dir()
    assert not (depot.racine / "core").exists(), "les données de la copie ne sont pas touchées"
    assert depot.temoin(".etat").read_text(encoding="utf-8") == "51\tpeuple\n"


def test_une_stack_qui_ne_demarre_pas_ne_laisse_rien_derriere(depot: DepotRelecture) -> None:
    """Le chemin d'échec est celui qui salit : la stack est arrêtée, et le témoin d'état ne reste
    pas — « injoignable » croirait ensuite couper une stack qui n'a jamais tourné."""
    depot.ports(ports_libres(), 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue("52", env={"MAESTRO_FAUX_START_CODE": "1"})
    assert resultat.returncode == 1
    assert depot.appels_start()[-1].startswith("--stop"), "la stack est arrêtée avant qu'on parte"
    assert not depot.temoin(".etat").exists()


def test_sans_etat_du_banc_le_peuple_se_nomme_et_rien_ne_se_rejoue(depot: DepotRelecture) -> None:
    """Aucun passage n'a laissé d'état sur le poste (code `4` du lanceur) : en jouer un coûte du
    vrai modèle, des dizaines de minutes — ça se DEMANDE, ça ne se décide pas au détour d'une
    relecture. Le geste est nommé, jamais joué."""
    depot.ports(ports_libres(), 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue("53", env={"MAESTRO_FAUX_START_CODE": "4"})
    assert resultat.returncode == 1
    assert "aucun passage du banc n'a laissé d'état sur ce poste" in resultat.stdout
    assert "bash scripts/controltower/start.sh --etat-banc --rejouer" in resultat.stdout
    assert all("--rejouer" not in appel for appel in depot.appels_start()), "rien n'est rejoué"


def test_les_projets_a_poser_viennent_de_l_api_avec_leurs_runs(tmp_path: Path) -> None:
    """Sans projet actif, le shell reste sur sa porte (#279) : la session pose un identifiant, et il
    vient de l'API réelle — l'état du banc en porte un par scénario joué —, avec le nombre de runs
    qui dit dans lequel un écran a quelque chose à montrer. Plus jamais une constante."""
    depot = DepotRelecture(tmp_path / "depot")
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    projets = (
        {"id": "prj-s1abc", "nom": "S1 — vider", "racine": "C:/a"},
        {"id": "prj-s2def", "nom": "S2 — application", "racine": "C:/b"},
    )
    with FausseApi(api, espace="1165-copie.banc", projets=projets, runs={"prj-s2def": 3}):
        # Le banc servi sur ce port est celui d'une relecture précédente : elle est à nous.
        resultat = depot.joue("54", env={"PATH": python_sur_le_chemin(tmp_path / "bin")})
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert "prj-s1abc        S1 — vider — 0 run(s)" in resultat.stdout
    assert "prj-s2def        S2 — application — 3 run(s)" in resultat.stdout
    assert depot.appels_start() == [
        f"--stop\tapi={api}\tui=3036",
        f"--etat-banc --no-browser\tapi={api}\tui=3036",
    ], "une API de ce banc restée d'un état précédent est arrêtée avant de rouvrir"


def test_une_stack_sur_les_donnees_de_la_copie_n_est_pas_soldee(tmp_path: Path) -> None:
    """Contre-exemple du précédent : la stack que la session a lancée sur SA copie n'est pas celle
    du banc. `--stop` solderait ses runs ; le lanceur la REMPLACE, sans rien solder (#441)."""
    depot = DepotRelecture(tmp_path / "depot")
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    with FausseApi(api, espace="1165-copie"):
        resultat = depot.joue("55", env={"PATH": python_sur_le_chemin(tmp_path / "bin")})
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert depot.appels_start() == [f"--etat-banc --no-browser\tapi={api}\tui=3036"]


def test_l_etat_vide_est_une_stack_neuve_et_un_projet_declare_par_l_api(tmp_path: Path) -> None:
    """Une stack NEUVE (`--etat-neuf`), puis un projet neuf déclaré par l'API — `origine: nouveau`,
    donc la validation des racines du produit et le dossier créé par lui —, sous l'atelier des
    relectures (jamais `AppData` ni le dépôt, qu'EF-38 refuse). Ses captures ont leur
    sous-dossier."""
    depot = DepotRelecture(tmp_path / "depot")
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    with FausseApi(api) as fausse:
        resultat = depot.joue(
            "56", "--etat", "vide", env={"PATH": python_sur_le_chemin(tmp_path / "bin")}
        )
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert depot.appels_start() == [f"--etat-neuf --no-browser\tapi={api}\tui=3036"]
    [declaration] = fausse.declarations()
    assert declaration["origine"] == "nouveau"
    assert declaration["racine"].replace("\\", "/").endswith("atelier-relecture/56/projet-neuf")
    assert "« Projet neuf » déclaré par l'API — prj-neuf1" in resultat.stdout
    assert (depot.racine / ".maestro" / "relecture" / "56" / "vide").is_dir()
    assert depot.temoin(".etat").read_text(encoding="utf-8") == "56\tvide\n"


def test_une_racine_refusee_par_l_api_se_dit_avec_son_motif(tmp_path: Path) -> None:
    """La validation est celle du produit : un refus n'est ni contourné ni tu, et la stack reste
    montée — l'écran montrera sa porte, ce qui se regarde aussi."""
    depot = DepotRelecture(tmp_path / "depot")
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    with FausseApi(api, refus="Racine refusée : AppData"):
        resultat = depot.joue(
            "57", "--etat", "vide", env={"PATH": python_sur_le_chemin(tmp_path / "bin")}
        )
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert "non déclaré — refusé par l'API (422) : Racine refusée : AppData" in resultat.stdout


def test_injoignable_coupe_l_api_des_deux_stacks_sans_rien_remonter(depot: DepotRelecture) -> None:
    """La vraie panne (#996) : l'API COUPÉE sous la stack montée, l'UI encore servie — jamais une
    stack remontée pour l'occasion, et l'avant tombe avec l'après (même état, des deux côtés)."""
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    assert depot.joue("58").returncode == 0
    depot.ecris(".maestro/relecture/.avant", f"58\tC:/avant-58\t{api + 200}\t3236\n")

    resultat = depot.joue("58", "--etat", "injoignable")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert depot.appels_start()[1:] == [
        f"--couper-api\tapi={api}\tui=3036",
        f"--couper-api\tapi={api + 200}\tui=3236",
    ]
    assert "l'API coupée sous l'état « peuple »" in resultat.stdout
    assert "PAR LE MENU" in resultat.stdout, "une navigation par l'URL renverrait à la porte"
    assert (depot.racine / ".maestro" / "relecture" / "58" / "injoignable").is_dir()


def test_injoignable_sans_stack_montee_ne_coupe_rien(depot: DepotRelecture) -> None:
    """Contre-exemple : sans stack montée par cette relecture, il n'y aurait rien à voir tomber —
    et couper les ports du worktree arrêterait peut-être la stack de quelqu'un d'autre."""
    depot.ports(8036, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    resultat = depot.joue("59", "--etat", "injoignable")
    assert resultat.returncode == 1
    assert "aucune stack montée par cette relecture" in resultat.stdout
    assert depot.appels_start() == []


def test_fin_arrete_la_stack_et_retire_ce_quelle_a_pose(tmp_path: Path) -> None:
    """Les stacks d'abord, puis le dossier du projet neuf — il sert encore une API vivante —, et le
    témoin d'état. La déclaration, elle, vit dans le banc, que le prochain état réécrit."""
    depot = DepotRelecture(tmp_path / "depot")
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("apps/web/app/runs/page.tsx", "// en cours\n")
    with FausseApi(api):
        depot.joue("60", "--etat", "vide", env={"PATH": python_sur_le_chemin(tmp_path / "bin")})
    neuf = depot.atelier / "60" / "projet-neuf"
    (neuf / ".maestro").mkdir(parents=True)  # ce que l'API y a créé, et ce qu'elle y a écrit
    (neuf / ".maestro" / "trace.txt").write_text("x\n", encoding="utf-8")

    resultat = depot.joue("--fin")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert depot.appels_start()[-1] == f"--stop\tapi={api}\tui=3036"
    assert not neuf.exists() and not neuf.parent.exists()
    assert not depot.temoin(".projet-neuf").exists()
    assert not depot.temoin(".etat").exists()


def test_fin_ne_retire_que_le_dossier_que_son_temoin_nomme_sous_l_atelier(
    depot: DepotRelecture,
) -> None:
    """C'est le témoin ET sa forme qui tranchent : un chemin qui n'est pas `…/<iid>/projet-neuf`
    n'est pas un projet neuf de relecture, et rien ne part."""
    etranger = depot.racine.parent / "mes-projets" / "compta"
    etranger.mkdir(parents=True)
    depot.ecris(".maestro/relecture/.projet-neuf", f"{etranger.as_posix()}\n")
    depot.joue("--fin")
    assert etranger.is_dir()
    assert not depot.temoin(".projet-neuf").exists(), "le témoin, lui, est soldé"


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
        (("42", "--etat"), "--etat attend un nom"),
        # Le geste de la démo (#978) : retiré par #1165, et DIT, plutôt qu'« option inconnue ».
        (("42", "--scenario", "vide"), "viennent de la vraie stack : --etat <nom>"),
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


def grille_remplie() -> str:
    """La grille du regard neuf (#980), chaque ligne répondue — lue dans LE fichier qui la porte.

    Depuis #980, `relecture-note` refuse (`5`) un jugement dont une ligne de la grille manque : un
    jugement de test qui veut atteindre la forge la porte donc, et la porte telle que le dépôt
    l'écrit — la recopier ici la ferait diverger de ce que le verbe vérifie. Les refus de la grille
    eux-mêmes sont éprouvés plus bas, section « La grille » (#974).
    """
    lignes = ["### Regard neuf — grille", "", "| Ligne | Réponse | Où |", "|---|---|---|"]
    grille = RACINE / "scripts" / "design" / "grille-relecture.tsv"
    for ligne in grille.read_text(encoding="utf-8").splitlines():
        if ligne and not ligne.startswith("#"):
            lignes.append(f"| {ligne.split(chr(9))[0]} | ✓ | vu |")
    return "\n".join(lignes) + "\n"


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
    fichier = jugement(forge, "vu.md", grille_remplie() + "Les deux thèmes vus, rien à signaler.\n")
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
    fichier = jugement(forge, "vu.md", grille_remplie() + "Rien à signaler.\n")
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
    fichier = jugement(
        forge, "vu.md", grille_remplie() + "Deuxième passage : le contraste est corrigé.\n"
    )
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
    acheve = forge.lib("relecture-note", "67", jugement(forge, "vu.md", grille_remplie() + "Vu.\n"))
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "introuvable" in acheve.stderr
    assert ecritures(forge) == []


def test_un_iid_qui_nen_est_pas_un_est_refuse_sans_rien_ecrire(forge: Depot) -> None:
    fichier = jugement(forge, "vu.md", grille_remplie() + "Vu.\n")
    acheve = forge.lib("relecture-note", "chat", fichier)
    assert acheve.returncode == 3
    assert forge.appels() == []


def test_une_forge_muette_ne_fait_pas_semblant_davoir_consigne(forge: Depot) -> None:
    """Un `1` **ne bloque pas la clôture** (le prompt le dit), mais il ne doit pas se faire passer
    pour un succès : ce que le dispositif rend difficile est l'absence de TRACE, jamais le merge."""
    forge.pose_etat(graphql=[{"contient": ["issue(number:68)"], "reponse": {"data": None}}])
    acheve = forge.lib("relecture-note", "68", jugement(forge, "vu.md", grille_remplie() + "Vu.\n"))
    assert acheve.returncode == 1
    assert ecritures(forge) == []


def test_la_consignation_ne_coute_quun_aller_de_lecture(forge: Depot) -> None:
    """Ce qui se garde est le NOMBRE d'allers, jamais une durée (règle de #577, #602).

    Un chronomètre en CI mesure la charge de la machine ; le compte, lui, porte la décision — le
    titre voyage avec les commentaires précisément pour que la question du ticket inconnu ne coûte
    pas un second appel.
    """
    forge.pose_etat(graphql=[regle_relecture("69")])
    forge.lib("relecture-note", "69", jugement(forge, "vu.md", grille_remplie() + "Vu.\n"))
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
    # L'étape 4ter (#968) la suit et parle comme elle — « on ne demande pas, on joue », « code
    # `3` » : la borne s'arrête donc à elle, sans quoi sa phrase garderait verte une 4bis qui
    # l'aurait perdue.
    fin = texte.index("4ter. **Le ticket fait-il ce qu'il disait ?**")
    return " ".join(texte[debut:fin].split())


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


# =================================================================================================
# La grille — ce que `relecture-note` refuse de consigner (#980)
# =================================================================================================
# La grille vit dans UN fichier, et le verbe est le seul de ses trois lecteurs qui GARDE : sans son
# refus, « la grille arrive sur le ticket » serait une règle lue — celle du skill. Ce qui se vérifie
# est une FORME, jamais un sens (#746) : une ligne de tableau par libellé, dont la réponse commence
# par ✓, ✗ ou « non vu ». Qu'un ✓ soit mérité, c'est le travail du regard neuf.
#
# Une `--raison` n'en porte pas — rien n'a été regardé — : c'est ce que
# `test_non_jouee_ne_ressemble_pas_a_regarde_rien_a_signaler` éprouve déjà, sans grille.


def libelles_grille(chemin: Path = GRILLE) -> list[str]:
    return [
        ligne.split("\t")[0]
        for ligne in chemin.read_text(encoding="utf-8").splitlines()
        if ligne and not ligne.startswith("#")
    ]


def lignes_manquantes(stderr: str) -> list[str]:
    """Les libellés que le refus nomme, une puce par ligne sans réponse."""
    return [
        ligne.strip().removeprefix("- ")
        for ligne in stderr.splitlines()
        if ligne.startswith("    - ")
    ]


def test_la_grille_du_depot_se_lit() -> None:
    """Le témoin de toute la section : une grille vide ou mal lue rendrait chaque refus trivial."""
    libelles = libelles_grille()
    assert len(libelles) >= 3, "grille vide ou mal lue : les tests de cette section seraient creux"
    assert len(set(libelles)) == len(libelles), "un libellé en double ne se vérifierait qu'une fois"


def test_un_jugement_sans_grille_est_refuse_avant_toute_lecture(forge: Depot) -> None:
    """Le texte libre d'avant #980 : refusé en `5`, sans aller vers la forge, chaque ligne nommée.

    Nommer les lignes est ce qui dit quoi rejouer ; les refuser avant la forge est la règle de
    `gl_reste_claude` — le contrôle gratuit d'abord, et un refus ne laisse rien derrière lui.
    """
    forge.pose_etat(graphql=[regle_relecture("80")])
    acheve = forge.lib("relecture-note", "80", jugement(forge, "libre.md", "Rien à signaler.\n"))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert forge.appels() == [], "le refus est gratuit : pas même une lecture"
    assert lignes_manquantes(acheve.stderr) == libelles_grille()
    # Contre-exemple : le même texte, grille remplie devant, passe — c'est la grille qui manquait.
    complet = jugement(forge, "complet.md", grille_remplie() + "Rien à signaler.\n")
    assert forge.lib("relecture-note", "80", complet).returncode == 0


def test_une_seule_ligne_tue_suffit_et_elle_seule_est_nommee(forge: Depot) -> None:
    """Oublier la ligne gênante est le contournement le plus probable — et le moins visible."""
    tue = libelles_grille()[-1]
    complet = grille_remplie()
    ampute = "".join(
        ligne + "\n" for ligne in complet.splitlines() if not ligne.startswith(f"| {tue} |")
    )
    assert ampute != complet, "la ligne n'a pas été retirée : le test ne prouverait rien"
    acheve = forge.lib("relecture-note", "81", jugement(forge, "vu.md", ampute))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert lignes_manquantes(acheve.stderr) == [tue]


@pytest.mark.parametrize(
    ("reponse", "code"),
    [
        ("✓", 0),
        ("✗ — `/couts`, sombre, nominal : le total disparaît", 0),
        ("non vu — état erreur non capturé", 0),
        ("OK", 5),
        ("vu", 5),
        ("", 5),
        ("à revoir ✓", 5),
    ],
)
def test_seuls_trois_mots_valent_reponse(forge: Depot, reponse: str, code: int) -> None:
    """✓, ✗ ou « non vu », en TÊTE de cellule. Une cellule vide est une question tue ; un « OK » est
    une réponse qu'aucune planche ni aucun lecteur ne sait ranger ; un ✓ en fin de phrase est un
    « peut-être » habillé."""
    forge.pose_etat(graphql=[regle_relecture("82")])
    libelles = libelles_grille()
    lignes = ["| Ligne | Réponse | Où |", "|---|---|---|"]
    lignes += [f"| {libelle} | ✓ | vu |" for libelle in libelles[:-1]]
    lignes.append(f"| {libelles[-1]} | {reponse} | vu |")
    acheve = forge.lib("relecture-note", "82", jugement(forge, "vu.md", "\n".join(lignes) + "\n"))
    assert acheve.returncode == code, acheve.stdout + acheve.stderr


def test_une_grille_hors_tableau_ne_compte_pas(forge: Depot) -> None:
    """La forme est un contrat : c'est le tableau que la planche rend et que le ticket affiche."""
    texte = "".join(f"- {libelle} : ✓\n" for libelle in libelles_grille())
    acheve = forge.lib("relecture-note", "83", jugement(forge, "vu.md", texte))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr


def test_la_grille_est_lue_dans_son_fichier_jamais_recopiee(forge: Depot) -> None:
    """Une ligne AJOUTÉE à la grille rend incomplet un jugement qui passait : le verbe la lit.

    C'est la garde d'« un seul endroit » : un verbe qui porterait sa propre liste laisserait la
    grille grandir sans que la nouvelle question arrive jamais sur un ticket.
    """
    forge.pose_etat(graphql=[regle_relecture("84")])
    complet = jugement(forge, "vu.md", grille_remplie() + "Vu.\n")
    assert forge.lib("relecture-note", "84", complet).returncode == 0, "témoin : complet avant"
    grille = forge.racine / "scripts" / "design" / "grille-relecture.tsv"
    with grille.open("a", encoding="utf-8", newline="\n") as flux:
        flux.write("Question ajoutée\tUne ligne de plus ?\tessai\n")
    acheve = forge.lib("relecture-note", "84", complet)
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert lignes_manquantes(acheve.stderr) == ["Question ajoutée"]


def test_une_grille_introuvable_est_une_panne_jamais_un_jugement_accepte(forge: Depot) -> None:
    """Un garde-fou qui saute est pire qu'un garde-fou absent : faute de grille, rien ne part."""
    forge.pose_etat(graphql=[regle_relecture("85")])
    fichier = jugement(forge, "vu.md", grille_remplie() + "Vu.\n")
    acheve = forge.lib(
        "relecture-note",
        "85",
        fichier,
        reglages={"GL_RELECTURE_GRILLE": "scripts/design/absente.tsv"},
    )
    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert "grille introuvable" in acheve.stderr
    assert forge.appels() == [], "ni lecture ni écriture : la vérification tombe avant la forge"


# =================================================================================================
# L'attente — la section « Rendu attendu » (#976), et ce que le regard neuf en reçoit (#980)
# =================================================================================================
# C'est la seule information qu'une session ne peut pas inventer, et c'est contre elle que l'écran
# est jugé. Elle a DEUX lecteurs et UN seul parseur : le brief de `/ticket-start` la relaie au
# cadrage, `relecture-attente` la tend au regard neuf — tous deux par `gl_issue_brief_render`, pour
# que la même section ne puisse pas dire deux choses selon qui la lit.

RUBRIQUES = ("Question", "Référence", "Ce qui ne bouge pas", "États à couvrir")
MOTIF_SECTION = re.compile(r"^## Rendu attendu[ \t]*$", re.M)
CRITERES = "## Critères d'acceptation\n\n- [ ] Le total se lit sans défiler\n"


def section_du_gabarit(nom: str) -> str:
    """La section d'un gabarit telle qu'une personne la reçoit — commentaire HTML compris."""
    texte = (GABARITS / nom).read_text(encoding="utf-8")
    debut = texte.index("## Rendu attendu")
    return texte[debut : texte.index("\n## ", debut + 1)].strip() + "\n"


def ticket_ecran(description: str) -> str:
    return corps_ticket(
        "Le total des coûts d'un coup d'œil",
        "agent::design, prio::moyenne, type::feature",
        description,
    )


def test_les_gabarits_d_ecran_portent_la_section_et_les_autres_non() -> None:
    """`feature` et `bug` peuvent toucher un écran ; `doc` et `infra`, non (#976).

    Les QUATRE rubriques sont celles que la saisine fait confronter une à une au regard neuf : une
    rubrique retirée du gabarit n'arriverait plus au jugement, une rubrique ajoutée n'y serait
    jamais confrontée. Les deux listes sont donc tenues ensemble, dans ce test.
    """
    fautif = (GABARITS / "doc.md").read_text(encoding="utf-8") + "\n## Rendu attendu\n"
    assert MOTIF_SECTION.search(fautif), "motif creux : l'absence vérifiée plus bas ne prouve rien"
    script = RELECTURE_SH.read_text(encoding="utf-8")
    for nom in ("feature.md", "bug.md"):
        section = section_du_gabarit(nom)
        for rubrique in RUBRIQUES:
            assert f"**{rubrique}**" in section, f"{nom} : la rubrique « {rubrique} » a disparu"
            assert f"| {rubrique} |  |  |" in script, (
                f"la saisine ne fait plus confronter « {rubrique} » au regard neuf"
            )
    for nom in ("doc.md", "infra.md"):
        assert not MOTIF_SECTION.search((GABARITS / nom).read_text(encoding="utf-8")), (
            f"{nom} porte un rendu attendu : un ticket de doc ou d'outillage n'a pas d'écran (#976)"
        )


def test_le_brief_rend_le_rendu_attendu_apres_les_criteres(forge: Depot) -> None:
    """Rendu APRÈS les critères, quelle que soit sa place dans le corps : c'est ce que
    `/ticket-start` relaie au cadrage sans relire le ticket (#602). Et le brief reste une
    projection."""
    rendu = (
        "## Rendu attendu\n"
        "- **Question** : combien a coûté ce run ?\n"
        "- **Référence** : comme la liste des runs de GitHub Actions, sans la colonne d'acteur\n"
    )
    forge.pose_etat(
        issues={"90": ticket_ecran("## Objectif\n\nVoir le coût.\n\n" + rendu + "\n" + CRITERES)}
    )
    acheve = forge.lib("issue-brief", "90")
    assert acheve.returncode == 0, acheve.stderr
    sortie = acheve.stdout
    assert "- **Question** : combien a coûté ce run ?" in sortie
    assert sortie.index("Le total se lit sans défiler") < sortie.index("## Rendu attendu")
    assert "Voir le coût." not in sortie, "l'objectif n'entre pas dans le brief : il reste compact"


def test_une_section_restee_telle_que_le_gabarit_la_pose_est_muette(forge: Depot) -> None:
    """Un ticket ouvert depuis l'interface web sans y toucher ne « porte » pas d'attente.

    La section vient du VRAI gabarit : c'est lui qui dit à quoi ressemble une section vide, et son
    commentaire HTML sur plusieurs lignes est précisément ce que le retrait doit savoir enlever.
    """
    for nom in ("feature.md", "bug.md"):
        forge.pose_etat(issues={"91": ticket_ecran(section_du_gabarit(nom) + "\n" + CRITERES)})
        sortie = forge.lib("issue-brief", "91").stdout
        assert "Le total se lit sans défiler" in sortie, "témoin : le brief est bien rendu"
        assert "Rendu attendu" not in sortie, f"{nom} : une section restée vide s'imprime"
        assert "<!--" not in sortie


def test_non_renseigne_n_est_pas_vide_et_s_imprime(forge: Depot) -> None:
    """La section « reste vide et le dit » (#976) — et la phrase est LUE dans `/ticket-create`, qui
    l'écrit : un brief qui ne la reconnaîtrait plus la ferait disparaître du cadrage."""
    trouve = re.search(r"`(_Non renseigné[^`]*_)`", PROMPT_CREATE.read_text(encoding="utf-8"))
    assert trouve, "la phrase « non renseigné » de /ticket-create a changé de forme"
    phrase = trouve.group(1)
    section = "## Rendu attendu\n<!-- le commentaire du gabarit -->\n" + phrase + "\n"
    forge.pose_etat(issues={"92": ticket_ecran(section + "\n" + CRITERES)})
    sortie = forge.lib("issue-brief", "92").stdout
    assert "## Rendu attendu" in sortie
    assert phrase in sortie


def test_une_rubrique_qui_cite_acceptation_ne_devient_pas_un_critere(forge: Depot) -> None:
    """L'ancre des critères est le mot « acceptation » : une rubrique qui le cite serait relayée
    deux fois, et au mauvais titre."""
    ligne = "- **Ce qui ne bouge pas** : les critères d'acceptation du lot 2, déjà livrés"
    forge.pose_etat(issues={"93": ticket_ecran("## Rendu attendu\n" + ligne + "\n\n" + CRITERES)})
    sortie = forge.lib("issue-brief", "93").stdout
    assert sortie.count(ligne) == 1, sortie
    assert sortie.index(ligne) > sortie.index("## Rendu attendu")
    # Contre-exemple : la même ligne HORS de la section est bien prise par l'ancre des critères —
    # sans lui, l'unicité ci-dessus ne prouverait rien.
    forge.pose_etat(issues={"94": ticket_ecran("## Notes\n" + ligne + "\n")})
    assert ligne in forge.lib("issue-brief", "94").stdout


def test_la_section_se_ferme_au_titre_suivant_jamais_a_un_sous_titre(forge: Depot) -> None:
    corps = (
        "## Rendu attendu\n"
        "- **Question** : où en est le run ?\n"
        "### États à couvrir\n"
        "- vide : le run n'a pas démarré\n"
        "## Notes techniques\n"
        "- le détail vient de /api/runs\n\n" + CRITERES
    )
    forge.pose_etat(issues={"95": ticket_ecran(corps)})
    sortie = forge.lib("issue-brief", "95").stdout
    assert "### États à couvrir" in sortie and "- vide : le run n'a pas démarré" in sortie
    assert "Notes techniques" not in sortie and "/api/runs" not in sortie


def test_ticket_create_decide_du_sort_de_la_section_par_jugement() -> None:
    """Les trois sorts (remplie, demandée, retirée), et le « non renseigné » sans répondant.

    Le tranchant est un JUGEMENT, jamais un lexique (#746) : « écran » dans une demande ne prouve
    rien. Le prompt doit le dire, faute de quoi la voie la plus simple — chercher des mots —
    revient.
    """
    texte = " ".join(PROMPT_CREATE.read_text(encoding="utf-8").split())
    assert "**La section « Rendu attendu »**" in texte
    assert "**retire la section entière**" in texte
    assert "**demande-le**" in texte
    assert "**Demander, jamais bloquer**" in texte
    assert "**Pas de lexique** pour le trancher (#746)" in texte


def test_ticket_start_relaie_le_rendu_attendu_sans_en_faire_une_pause() -> None:
    texte = " ".join(PROMPT_START.read_text(encoding="utf-8").split())
    etape = texte[texte.index("6. **Résumé court") : texte.index("7. **Variantes")]
    assert "**`## Rendu attendu`**" in etape
    assert "ne la réécris pas à ta façon" in etape
    assert "**n'est pas une pause**" in etape, (
        "un « non renseigné » qui arrêterait le démarrage bloquerait tout ticket né sans répondant"
    )


def regle_attente(
    iid: str, corps: str = "", notes: tuple[str, ...] = (), existe: bool = True
) -> dict:
    """Réponse à `relecture-attente` : description ET commentaires, dans un seul aller (#602)."""
    issue = (
        None
        if not existe
        else {
            "title": "Le total des coûts",
            "body": corps,
            "comments": {"nodes": [{"body": note} for note in notes]},
        }
    )
    return {
        "contient": [f"issue(number:{iid})", "title body comments(first: 100)"],
        "reponse": {"data": {"repository": {"issue": issue}}},
    }


def attente(sortie: str) -> tuple[str, list[str]]:
    """Les deux blocs de `relecture-attente` : le rendu attendu, puis les décisions une à une."""
    rendu, _, decisions = sortie.partition("@@decisions@@\n")
    if not decisions.strip():
        return rendu, []
    return rendu, [bloc.strip("\n") for bloc in decisions.split("@@decision@@\n")]


def test_l_attente_tire_le_rendu_attendu_par_le_parseur_du_brief(forge: Depot) -> None:
    corps = (
        "## Objectif\nVoir.\n\n"
        "## Rendu attendu\n<!-- commentaire\n     du gabarit -->\n- **Question** : combien ?\n\n"
        + CRITERES
    )
    forge.pose_etat(graphql=[regle_attente("96", corps)])
    acheve = forge.lib("relecture-attente", "96")
    assert acheve.returncode == 0, acheve.stderr
    rendu, decisions = attente(acheve.stdout)
    assert rendu.splitlines() == ["## Rendu attendu", "- **Question** : combien ?"]
    assert decisions == []
    # Même section vide que le brief : le gabarit laissé tel quel ne tend rien au regard neuf.
    forge.pose_etat(
        graphql=[regle_attente("96", section_du_gabarit("feature.md") + "\n" + CRITERES)]
    )
    assert attente(forge.lib("relecture-attente", "96").stdout)[0].strip() == ""


def test_seules_les_decisions_ancrees_en_tete_sont_tirees(forge: Depot) -> None:
    """Deux ancres, écrites par les commandes qui produisent la pièce — et RIEN d'autre du ticket.

    Un ticket porte aussi les notes de la session qui l'a écrit, c'est-à-dire son raisonnement :
    précisément ce que le regard neuf ne doit pas recevoir. Et une ancre CITÉE au milieu d'une note
    n'en fait pas une décision.
    """
    veille = "## Veille de conception — le total d'un run\n\n1. Le total en tête, jamais en pied."
    variante = "## Variante retenue\n\nB : la tuile seule, la colonne alourdissait."
    note_session = "Implémenté : j'ai préféré une grille CSS au tableau."
    citation = "Relu la section ## Veille de conception : rien à reprendre."
    notes = (note_session, veille, citation, variante)
    forge.pose_etat(graphql=[regle_attente("97", CRITERES, notes=notes)])
    acheve = forge.lib("relecture-attente", "97")
    assert acheve.returncode == 0, acheve.stderr
    assert attente(acheve.stdout)[1] == [veille, variante]
    # Contre-exemple : la citation, remise en tête, redevient une décision — l'ancre est bien lue.
    en_tete = citation.removeprefix("Relu la section ")
    forge.pose_etat(graphql=[regle_attente("97", CRITERES, notes=(en_tete,))])
    assert attente(forge.lib("relecture-attente", "97").stdout)[1] == [en_tete]


def test_un_commentaire_qui_ecrit_la_cle_body_ne_coupe_pas_sa_decision(forge: Depot) -> None:
    """Le découpage se fait sur le JSON, où un `{"body":"` écrit dans un commentaire est échappé."""
    variante = '## Variante retenue\n\nA, et le contrat reste {"body":"x"} tel quel.'
    forge.pose_etat(graphql=[regle_attente("98", notes=(variante,))])
    assert attente(forge.lib("relecture-attente", "98").stdout)[1] == [variante]


def test_l_attente_ne_coute_qu_un_aller_et_n_ecrit_rien(forge: Depot) -> None:
    """Le compte d'allers, jamais une durée (#577, #602) — et une lecture reste une lecture."""
    forge.pose_etat(graphql=[regle_attente("99", CRITERES, notes=("## Variante retenue\n\nA.",))])
    forge.lib("relecture-attente", "99")
    lectures = [ligne for ligne in forge.appels() if ligne.startswith("api\tgraphql")]
    assert len(lectures) == 1, lectures
    assert ecritures(forge) == []


@pytest.mark.parametrize(
    ("iid", "regle", "code"),
    [
        ("100", regle_attente("100", existe=False), 3),
        ("101", {"contient": ["issue(number:101)"], "reponse": {"data": None}}, 1),
        ("chat", None, 2),
    ],
    ids=["ticket-inconnu", "forge-muette", "pas-un-iid"],
)
def test_l_attente_ne_confond_pas_inconnu_muet_et_usage(
    forge: Depot, iid: str, regle: dict | None, code: int
) -> None:
    """Trois codes, parce que la saisine en dit trois choses : un `1` y devient « non vu »."""
    forge.pose_etat(graphql=[regle] if regle else [])
    acheve = forge.lib("relecture-attente", iid)
    assert acheve.returncode == code, acheve.stdout + acheve.stderr
    if code == 2:
        assert forge.appels() == [], "un usage fautif ne coûte pas un aller"


# =================================================================================================
# L'avant — origin/main, servi à côté de la branche et jamais à sa place (#977, docs/30 §5.6)
# =================================================================================================
# Le décor n'a pas de remote : `origin/main` y est une ref figée sur HEAD, et c'est tout ce que le
# script lit — `rev-parse` pour la trouver, `ls-tree` pour ses pages. Ce qu'origin/main sait
# SERVIR, c'est son lanceur qui le dit : le double de `worktree.sh` le recopie d'origin/main, pas
# de l'arbre.

#: Le double de `worktree.sh avant` : il journalise ce qu'on lui demande, monte un « avant » à côté
#: du décor — avec le `start.sh` d'ORIGIN/MAIN, pour que l'avant se lance par SON lanceur — et sait
#: échouer au montage comme au retrait.
FAUX_WORKTREE = """#!/usr/bin/env bash
racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$racine/.maestro"
printf '%s\\n' "$*" >>"$racine/.maestro/worktree.log"
if [ "$2" = --retirer ]; then exit "${MAESTRO_FAUX_RETRAIT_CODE:-0}"; fi
if [ -n "${MAESTRO_FAUX_MONTAGE_ECHEC:-}" ]; then echo "  ✗ $MAESTRO_FAUX_MONTAGE_ECHEC"; exit 1; fi
iid="${*: -1}"
dest="$(dirname "$racine")/avant-$iid"
mkdir -p "$dest/scripts/controltower"
git -C "$racine" show origin/main:scripts/controltower/start.sh \\
  >"$dest/scripts/controltower/start.sh"
echo "AVANT $dest"
"""


def lances(appels: list[str]) -> list[str]:
    """Les appels qui LANCENT une stack — sans les questions posées au lanceur (son diagnostic)."""
    return [appel for appel in appels if "--diagnostic-navigateur" not in appel]


def ecran_retouche(depot: DepotRelecture, route: str = "couts") -> None:
    """Un écran présent sur origin/main, retouché par le ticket — le cas qui a un avant."""
    page = f"apps/web/app/{route}/page.tsx"
    depot.commit("feat: l'écran existe\n\nRefs #1", [page])
    depot.origin_main()
    depot.ecris(page, "// retouché par le ticket\n")


def journal_worktree(depot: DepotRelecture) -> list[str]:
    journal = depot.racine / ".maestro" / "worktree.log"
    return journal.read_text(encoding="utf-8").splitlines() if journal.exists() else []


def temoin_avant(depot: DepotRelecture) -> Path:
    return depot.racine / ".maestro" / "relecture" / ".avant"


def test_un_ecran_existant_a_son_avant_sur_les_ports_decales_de_200(depot: DepotRelecture) -> None:
    """Dérivés de l'APRÈS, pas de l'iid : un `--ports` imposé au montage emporte l'avant avec lui.

    Et le TSV porte l'avant en DERNIÈRE colonne : les quatre premières sont lues par leur rang.
    """
    depot.ports(8036, 3036)
    ecran_retouche(depot)
    assert tsv(depot.joue("--plan", "--tsv", "110").stdout) == [
        (
            "/couts",
            "http://localhost:3036/couts",
            "direct",
            "apps/web/app/couts/page.tsx",
            "http://localhost:3236/couts",
        )
    ]
    assert "avant : http://localhost:3236/couts" in depot.joue("--plan", "110").stdout


def test_un_ecran_nouveau_est_nomme_jamais_cherche_sur_une_404(depot: DepotRelecture) -> None:
    ecran_retouche(depot)
    depot.ecris("apps/web/app/couts/page.tsx", "// apps/web/app/couts/page.tsx\n")  # rendu intact
    depot.ecris("apps/web/app/agents/page.tsx", "// un écran neuf\n")
    lignes = tsv(depot.joue("--plan", "--tsv", "111").stdout)
    assert [(ligne[0], ligne[-1]) for ligne in lignes] == [("/agents", "nouveau")]
    assert "écran NOUVEAU" in depot.joue("--plan", "111").stdout


def test_une_page_sous_un_segment_dynamique_ne_fait_pas_exister_sa_liste(
    depot: DepotRelecture,
) -> None:
    """`/runs/[runId]/page.tsx` se range sous `/runs` sans que `/runs` réponde : pas d'avant."""
    depot.commit("feat: le détail\n\nRefs #1", ["apps/web/app/runs/[runId]/page.tsx"])
    depot.origin_main()
    depot.ecris("apps/web/app/runs/page.tsx", "// la liste, neuve\n")
    assert tsv(depot.joue("--plan", "--tsv", "112").stdout)[0][-1] == "nouveau"
    # Contre-exemple : la liste présente sur origin/main a bien son avant.
    ecran_retouche(depot, "runs")
    assert tsv(depot.joue("--plan", "--tsv", "112").stdout)[0][-1] == "http://localhost:3200/runs"


def test_l_avant_eteint_se_dit_au_lieu_de_se_taire(depot: DepotRelecture) -> None:
    """Ne pas payer l'avant est un choix, pas un oubli — et le plan le dit."""
    ecran_retouche(depot)
    eteint = {"MAESTRO_RELECTURE_AVANT": "0"}
    assert tsv(depot.joue("--plan", "--tsv", "113", env=eteint).stdout)[0][-1] == "-"
    assert "éteint (MAESTRO_RELECTURE_AVANT=0)" in depot.joue("--plan", "113", env=eteint).stdout
    # Contre-exemple : sans l'interrupteur, le même plan porte son avant.
    assert tsv(depot.joue("--plan", "--tsv", "113").stdout)[0][-1].startswith("http://")


def test_sans_origin_main_l_avant_est_indisponible_et_le_dit(depot: DepotRelecture) -> None:
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    sortie = depot.joue("--plan", "114").stdout
    assert "avant      : indisponible — origin/main introuvable dans ce dépôt" in sortie


def test_l_avant_se_monte_a_cote_et_se_lance_par_son_propre_lanceur(
    depot: DepotRelecture, tmp_path: Path
) -> None:
    """Le chemin nominal de bout en bout, préparation puis `--fin`.

    Ce qui se garde : l'avant est monté par `worktree.sh avant` et servi par SON `start.sh`, sur
    SES ports, dans LE MÊME ÉTAT que l'après — l'état du banc, rouvert des deux côtés du même
    passage ; l'arbre du ticket n'est pas touché ; `--fin` arrête les DEUX stacks, l'avant
    d'abord, puis retire l'avant et son témoin — `--fin` ne prend pas d'iid, c'est le témoin qui
    sait quoi retirer.
    """
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("scripts/git/worktree.sh", FAUX_WORKTREE)
    ecran_retouche(depot)
    avant = tmp_path / "avant-115"

    prepare = depot.joue("115")
    assert prepare.returncode == 0, prepare.stdout + prepare.stderr
    assert journal_worktree(depot) == ["avant --sans-fetch 115"]
    assert lances(depot.appels_start(avant)) == [
        f"--etat-banc --no-browser\tapi={api + 200}\tui=3236"
    ]
    assert depot.appels_start() == [f"--etat-banc --no-browser\tapi={api}\tui=3036"], (
        "l'après n'a pas bougé"
    )
    assert "avant prêt" in prepare.stdout
    iid, _chemin, api_avant, ui = (
        temoin_avant(depot).read_text(encoding="utf-8").rstrip("\n").split("\t")
    )
    assert (iid, api_avant, ui) == ("115", str(api + 200), "3236")
    diff = depot._git("diff", "--name-only").stdout.split()
    assert diff == ["apps/web/app/couts/page.tsx"], "aucun fichier suivi du ticket n'est touché"

    fin = depot.joue("--fin")
    assert fin.returncode == 0, fin.stdout + fin.stderr
    arrets = [ligne for ligne in depot.appels_start() if ligne.startswith("--stop")]
    assert arrets == [f"--stop\tapi={api + 200}\tui=3236", f"--stop\tapi={api}\tui=3036"]
    assert journal_worktree(depot)[-1] == "avant --retirer 115"
    assert not temoin_avant(depot).exists()


def test_un_retrait_rate_garde_le_temoin_pour_le_fin_suivant(depot: DepotRelecture) -> None:
    """Le témoin ne part qu'avec l'avant : un `--fin` rejoué retrouve ce qui reste à retirer."""
    depot.ecris("scripts/git/worktree.sh", FAUX_WORKTREE)
    ecran_retouche(depot)
    assert depot.joue("116").returncode == 0
    depot.joue("--fin", env={"MAESTRO_FAUX_RETRAIT_CODE": "1"})
    assert temoin_avant(depot).exists()
    depot.joue("--fin")
    assert not temoin_avant(depot).exists()
    assert journal_worktree(depot)[-2:] == ["avant --retirer 116", "avant --retirer 116"]


@pytest.mark.parametrize("cause", ["script-absent", "montage-en-echec"])
def test_un_avant_indisponible_ne_vaut_jamais_une_relecture_manquante(
    depot: DepotRelecture, cause: str
) -> None:
    """Best-effort de bout en bout : l'après reste prêt, la cause est dite, rien n'est laissé."""
    api = ports_libres()
    depot.ports(api, 3036)
    env: dict[str, str] = {}
    if cause == "montage-en-echec":
        depot.ecris("scripts/git/worktree.sh", FAUX_WORKTREE)
        env["MAESTRO_FAUX_MONTAGE_ECHEC"] = "origin/main introuvable — pas d'avant à monter"
    ecran_retouche(depot)
    resultat = depot.joue("117", env=env)
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert depot.appels_start() == [f"--etat-banc --no-browser\tapi={api}\tui=3036"]
    assert not temoin_avant(depot).exists()
    if cause == "script-absent":
        assert (
            "indisponible — scripts/git/worktree.sh introuvable ; l'après seul" in resultat.stdout
        )
    else:
        assert "montage en échec : ✗ origin/main introuvable" in resultat.stdout
        assert "l'après reste prêt" in resultat.stdout


def test_l_avant_sert_le_meme_etat_ou_rien(tmp_path: Path) -> None:
    """Comparer une stack neuve à un état peuplé ferait voir une différence que le ticket n'a pas
    faite. C'est le LANCEUR d'origin/main qui dit s'il sait servir l'état, par son diagnostic —
    aucune option n'est cherchée dans son source. Refusé : l'après seul, et dit."""
    depot = DepotRelecture(tmp_path / "depot")
    api = ports_libres()
    depot.ports(api, 3036)
    depot.ecris("scripts/git/worktree.sh", FAUX_WORKTREE)
    # origin/main porte un lanceur plus ancien, qui ne connaît pas la stack neuve ; la branche, le
    # lanceur d'aujourd'hui.
    depot.ecris("scripts/controltower/start.sh", faux_start(refuse="--etat-neuf"))
    depot._git("add", "scripts/controltower/start.sh")
    depot._git("commit", "-m", "chore: un lanceur ancien\n\nRefs #1")
    ecran_retouche(depot)
    depot.ecris("scripts/controltower/start.sh", FAUX_START)

    resultat = depot.joue("118", "--etat", "vide")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert (
        "origin/main ne sait pas servir l'état « vide » (son lanceur refuse --etat-neuf) — "
        "l'après seul pour cet état"
    ) in resultat.stdout
    assert depot.appels_start(tmp_path / "avant-118") == [], "rien n'est lancé côté avant"

    # Contre-exemple : le lanceur arrivé sur origin/main, l'avant est lancé DANS cet état.
    depot._git("add", "scripts/controltower/start.sh")
    depot._git("commit", "-m", "chore: la stack neuve\n\nRefs #1")
    depot.origin_main()
    assert depot.joue("118", "--etat", "vide").returncode == 0
    assert lances(depot.appels_start(tmp_path / "avant-118")) == [
        f"--etat-neuf --no-browser\tapi={api + 200}\tui=3236"
    ]


# =================================================================================================
# Les états — ceux de la vraie stack, montés par `--etat`, comptés par `--couverture` (#978, #1165)
# =================================================================================================
# L'état peuplé est rarement celui qui casse. De #978 à #1165, la démo SIMULAIT les autres ; ils
# viennent désormais de la vraie stack, et ceux qu'elle ne produit pas sont NOMMÉS, jamais imités.


def non_couverts(depot: DepotRelecture, iid: str) -> dict[str, str]:
    """Les états non couverts, tels que le plan TSV les rend à un appelant machine."""
    lignes = depot.joue("--plan", "--tsv", iid).stdout.splitlines()
    return {
        champs[1]: champs[2]
        for champs in (ligne.split("\t") for ligne in lignes if ligne.startswith("# non-couvert\t"))
    }


def test_le_plan_annonce_les_etats_de_la_vraie_stack_et_ceux_qu_elle_ne_produit_pas(
    depot: DepotRelecture,
) -> None:
    """Chaque état se dit avec ce qu'il montre, et ce que la vraie stack ne sait pas produire se
    nomme avec sa raison — le magasin coupé mesuré, la charge qu'on ne gonfle pas."""
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    plan = depot.joue("--plan", "120").stdout
    for etat in etats_du_script():
        assert re.search(rf"^ +{etat} +\S", plan, re.M), f"l'état « {etat} » n'est pas annoncé"
    assert f"# etats\t{' '.join(etats_du_script())}" in depot.joue(
        "--plan", "--tsv", "120"
    ).stdout.splitlines()
    manquants = non_couverts(depot, "120")
    assert set(manquants) == {"erreur", "charge"}
    assert "ce script ne la monte pas encore" in manquants["erreur"]
    assert "rien n'est gonflé" in manquants["charge"]
    assert "non couverts" in plan and "jamais à imiter" in plan
    assert "démo" not in plan.lower() and "demo" not in plan.lower(), "plus un mot de la démo"


@pytest.mark.parametrize("etat", ["erreur", "charge"])
def test_un_etat_non_couvert_se_refuse_avec_sa_raison(depot: DepotRelecture, etat: str) -> None:
    """Demander un état que la vraie stack ne produit pas ne monte rien : la réponse est celle du
    plan — sa raison, et l'endroit où il se nomme."""
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    raison = non_couverts(depot, "121")[etat]
    refus = depot.joue("121", "--etat", etat)
    assert refus.returncode == 2, refus.stdout + refus.stderr
    assert f"l'état « {etat} » n'est pas couvert — {raison}" in refus.stderr
    assert "ce que je n'ai pas pu voir" in refus.stderr
    assert depot.appels_start() == []


def test_un_etat_inconnu_est_refuse_avant_la_stack(depot: DepotRelecture) -> None:
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    refus = depot.joue("122", "--etat", "plein")
    assert refus.returncode == 2, refus.stdout + refus.stderr
    assert "état inconnu « plein » (la vraie stack sert : peuple vide injoignable)" in refus.stderr
    assert depot.appels_start() == []


@pytest.mark.parametrize(
    "args",
    [
        ("--plan", "123"),
        ("--couverture", "123"),
        ("--saisine", "123"),
        ("--planche", "123"),
        ("--fin",),
    ],
)
def test_l_etat_ne_vaut_que_pour_monter_la_stack(
    depot: DepotRelecture, args: tuple[str, ...]
) -> None:
    """Le plan, la couverture, la saisine et la planche valent pour TOUS les états ; `--fin` arrête
    la stack quel que soit le sien. Un `--etat` là serait une question mal posée."""
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    refus = depot.joue(*args, "--etat", "vide")
    assert refus.returncode == 2, refus.stdout + refus.stderr
    assert depot.appels_start() == []


def test_un_etat_a_son_sous_dossier_et_le_defaut_garde_le_sien(depot: DepotRelecture) -> None:
    """Deux états d'un même écran ne s'écrasent pas — et l'état par défaut garde le chemin d'avant
    #978, le dossier du ticket lui-même."""
    depot.ports(ports_libres(), 3036)
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    assert depot.joue("124", "--etat", "vide").returncode == 0
    assert (depot.racine / ".maestro" / "relecture" / "124" / "vide").is_dir()
    assert depot.joue("124", "--etat", "peuple").returncode == 0
    assert not (depot.racine / ".maestro" / "relecture" / "124" / "peuple").exists()
    assert lances(depot.appels_start())[-1].startswith("--etat-banc --no-browser\t")


def test_la_couverture_compte_ce_qui_est_sur_le_disque_et_nomme_ce_qui_ne_s_y_trouvera_pas(
    depot: DepotRelecture,
) -> None:
    """Elle CONSTATE : une capture vide n'est pas un regard, un avant n'est pas un regard sur la
    branche — et elle ne démarre ni n'écrit rien. Les états que la vraie stack ne produit pas y
    sont NOMMÉS : c'est elle que le jugement recopie, et c'est ce qui les fait arriver dans la note
    de relecture (#1165)."""
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    base = ".maestro/relecture/125"
    depot.capture(f"{base}/couts-clair.png")
    depot.capture(f"{base}/couts-sombre.png")
    depot.capture(f"{base}/vide/couts-clair.png")
    depot.capture(f"{base}/injoignable/couts-clair.png", b"")
    depot.capture(f"{base}/injoignable/couts-sombre-avant.png")
    present = sorted((depot.racine / ".maestro").rglob("*"))

    resultat = depot.joue("--couverture", "--tsv", "125")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert tsv(resultat.stdout) == [
        ("/couts", "couts", "peuple", "1", "1"),
        ("/couts", "couts", "vide", "1", "0"),
        ("/couts", "couts", "injoignable", "0", "0"),
    ]
    assert "# non-couvert\terreur\t" in resultat.stdout
    lisible = depot.joue("--couverture", "125").stdout
    assert "une capture n'est pas un regard" in lisible
    assert "non couverts" in lisible and "ce que je n'ai pas pu voir" in lisible
    for etat, raison in non_couverts(depot, "125").items():
        assert f"{etat}" in lisible and raison in lisible
    assert depot.appels_start() == []
    assert sorted((depot.racine / ".maestro").rglob("*")) == present, "la couverture n'écrit rien"


def test_sans_ecran_rien_a_couvrir(depot: DepotRelecture) -> None:
    depot.commit("feat: moteur\n\nRefs #126", ["maestro/engine.py"])
    assert depot.joue("--couverture", "126").returncode == 3


# =================================================================================================
# La saisine et la planche — ce que le regard neuf reçoit, ce qu'une personne voit (#980)
# =================================================================================================
# La saisine rend « ne reçoit que » VÉRIFIABLE : le prompt du sous-agent n'est que son chemin, et
# le fichier reste sur le disque. `lib.sh` y est un double — sa `relecture-attente` est éprouvée
# pour de vrai plus haut ; ce qui se teste ici est ce que la saisine en FAIT.

FAUSSE_LIB = """#!/usr/bin/env bash
racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[ "$1" = relecture-attente ] || exit 2
cat "$racine/.maestro/attente.txt" 2>/dev/null
exit "${MAESTRO_FAUSSE_ATTENTE_CODE:-0}"
"""

ATTENTE = (
    "## Rendu attendu\n"
    "- **Question** : combien a coûté ce run ?\n"
    "@@decisions@@\n"
    "## Veille de conception — le total\n"
    "\n"
    "1. Le total en tête.\n"
    "@@decision@@\n"
    "## Variante retenue\n"
    "\n"
    "B.\n"
)


def depot_regard(tmp_path: Path, attente_du_ticket: str = "@@decisions@@\n") -> DepotRelecture:
    """Un écran retouché (`/couts`, qui a un avant) et un écran neuf (`/agents`, qui n'en a pas)."""
    depot = DepotRelecture(tmp_path / "depot")
    depot.recopie(GRILLE)
    depot.ecris("scripts/gitlab/lib.sh", FAUSSE_LIB)
    depot.ecris(".maestro/attente.txt", attente_du_ticket)
    ecran_retouche(depot)
    depot.ecris("apps/web/app/agents/page.tsx", "// un écran neuf\n")
    return depot


def chemin_rendu(sortie: str, prefixe: str) -> str:
    derniere = sortie.strip().splitlines()[-1]
    assert derniere.startswith(prefixe + " "), sortie
    return derniere.removeprefix(prefixe + " ")


def test_sans_capture_ni_saisine_ni_planche(tmp_path: Path) -> None:
    """`4` : la relecture n'a pas eu lieu et sa raison se consigne — rien n'est écrit ici."""
    depot = depot_regard(tmp_path)
    for mode in ("--saisine", "--planche"):
        resultat = depot.joue(mode, "130")
        assert resultat.returncode == 4, resultat.stdout + resultat.stderr
        assert "relecture-note --raison" in resultat.stderr
    assert not (depot.racine / ".maestro" / "relecture").exists()


def test_sans_ecran_rien_a_juger(depot: DepotRelecture) -> None:
    depot.commit("feat: moteur\n\nRefs #131", ["maestro/engine.py"])
    for mode in ("--saisine", "--planche"):
        assert depot.joue(mode, "131").returncode == 3


def test_la_saisine_porte_les_pieces_et_rien_d_autre(tmp_path: Path) -> None:
    """Les paires en chemins absolus (l'outil `Read` n'en prend pas d'autres), l'attente citée telle
    qu'elle est écrite, la grille lue dans son fichier — et ni le code, ni le diff."""
    depot = depot_regard(tmp_path, ATTENTE)
    base = ".maestro/relecture/132"
    depot.capture(f"{base}/couts-clair.png")
    depot.capture(f"{base}/couts-clair-avant.png")
    depot.capture(f"{base}/agents-clair.png")

    resultat = depot.joue("--saisine", "132")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    chemin = chemin_rendu(resultat.stdout, "SAISINE")
    assert chemin.endswith("/.maestro/relecture/132/saisine.md")
    racine = chemin.removesuffix("/.maestro/relecture/132/saisine.md")
    saisine = (depot.racine / base / "saisine.md").read_text(encoding="utf-8")

    assert f"`{racine}/{base}/couts-clair.png`" in saisine
    assert f"`{racine}/{base}/couts-clair-avant.png`" in saisine
    assert "| `/couts` | sombre | non capturé | non capturé |" in saisine
    assert "| `/agents` | sombre | non capturé | écran nouveau — aucun avant |" in saisine
    assert "### État « vide » — aucune capture" in saisine, "ce qui manque se nomme"

    assert "> - **Question** : combien a coûté ce run ?" in saisine
    assert (
        "> ## Veille de conception — le total\n>\n> 1. Le total en tête.\n\n> ## Variante"
        in saisine
    )
    for libelle in libelles_grille():
        assert f"**{libelle}**" in saisine and f"| {libelle} |  |  |" in saisine
    for rubrique in RUBRIQUES:
        assert f"| {rubrique} |  |  |" in saisine, (
            "un rendu attendu écrit se confronte rubrique par rubrique"
        )

    assert "apps/web/app/couts/page.tsx" in depot.joue("--plan", "132").stdout, (
        "témoin : le plan le nomme"
    )
    assert "apps/web/" not in saisine, "le diff n'entre pas dans la saisine"


def test_une_forge_muette_ne_bloque_pas_la_saisine(tmp_path: Path) -> None:
    """La saisine le DIT, et les rubriques iront à « non vu » — jamais un « rien à confronter »."""
    depot = depot_regard(tmp_path)
    depot.capture(".maestro/relecture/133/couts-clair.png")
    resultat = depot.joue("--saisine", "133", env={"MAESTRO_FAUSSE_ATTENTE_CODE": "1"})
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert "ticket illisible" in resultat.stdout
    saisine = (depot.racine / ".maestro" / "relecture" / "133" / "saisine.md").read_text(
        encoding="utf-8"
    )
    assert "Le ticket n'a pas pu être lu (lib.sh relecture-attente : code 1)" in saisine
    assert "Rendu attendu illisible sur le ticket : non confronté." in saisine
    assert "rien à confronter" not in saisine, (
        "un ticket illisible n'est pas un ticket sans attente"
    )


def test_sans_grille_pas_de_saisine(tmp_path: Path) -> None:
    """Une saisine sans grille ferait rendre un texte libre — c'est-à-dire ce que #980 retire."""
    depot = depot_regard(tmp_path)
    (depot.racine / "scripts" / "design" / "grille-relecture.tsv").unlink()
    depot.capture(".maestro/relecture/134/couts-clair.png")
    resultat = depot.joue("--saisine", "134")
    assert resultat.returncode == 1
    assert "grille introuvable" in resultat.stderr
    assert not (depot.racine / ".maestro" / "relecture" / "134" / "saisine.md").exists()


def test_des_partis_pris_sans_ancre_se_citent_tels_quels(tmp_path: Path) -> None:
    """Une veille consignée avant l'ancre se recopie « sans un mot de plus » : le seul texte de la
    session qui entre dans la saisine, et il n'y entre que cité."""
    depot = depot_regard(tmp_path)
    depot.capture(".maestro/relecture/135/couts-clair.png")
    depot.ecris(
        ".maestro/session/veille.md", "Partis pris du 2026-08-30 :\n\n1. Une colonne, pas deux.\n"
    )
    assert (
        depot.joue("--plan", "135", "--partis-pris", ".maestro/session/veille.md").returncode == 2
    )
    assert (
        depot.joue("--saisine", "135", "--partis-pris", ".maestro/session/absent.md").returncode
        == 2
    )
    resultat = depot.joue("--saisine", "135", "--partis-pris", ".maestro/session/veille.md")
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    saisine = (depot.racine / ".maestro" / "relecture" / "135" / "saisine.md").read_text(
        encoding="utf-8"
    )
    assert "> Partis pris du 2026-08-30 :\n>\n> 1. Une colonne, pas deux." in saisine
    assert "tenu · plié · non vu" in saisine, "les partis pris cités se confrontent"


def python_sur_le_chemin(dossier: Path) -> str:
    """Un `python3` qui mène à l'interpréteur des tests : un décor n'a pas de venv, et la planche
    n'importe que la bibliothèque standard — c'est ce qui lui permet de tourner sans lui."""
    dossier.mkdir(parents=True, exist_ok=True)
    lanceur = dossier / "python3"
    interpreteur = sys.executable.replace("\\", "/")
    lanceur.write_text(
        f'#!/usr/bin/env bash\nexec "{interpreteur}" "$@"\n', encoding="utf-8", newline="\n"
    )
    lanceur.chmod(0o755)
    return str(dossier) + os.pathsep + os.environ.get("PATH", "")


def test_la_planche_est_autonome_et_survit_au_ramassage_du_worktree(tmp_path: Path) -> None:
    """`/ticket-finish` ramasse le worktree juste après le merge, AVANT son résumé : une planche
    laissée là serait un lien mort au moment où on le lit. La dernière ligne nomme donc la copie du
    CLONE PRINCIPAL — et la planche tient en un fichier, captures en `data:`, jugement en tête."""
    depot = DepotRelecture(tmp_path / "depot")
    depot.recopie(PLANCHE_PY, BUILD_PY)
    depot.ecris("apps/web/app/couts/page.tsx", "// l'écran\n")
    depot._git("add", "-A")
    depot._git("commit", "-m", "chore: outillage de la planche\n\nRefs #1")
    arbre = tmp_path / "worktree"
    depot._git("worktree", "add", "-b", "feat/136-total", str(arbre))
    (arbre / "apps" / "web" / "app" / "couts" / "page.tsx").write_text(
        "// retouché\n", encoding="utf-8"
    )
    base = arbre / ".maestro" / "relecture" / "136"
    base.mkdir(parents=True)
    (base / "couts-clair.png").write_bytes(PNG)
    (base / "jugement.md").write_text(
        "### Regard neuf — grille\n\n| Ligne | Réponse | Où |\n|---|---|---|\n"
        "| Deux thèmes | ✗ — `/couts`, sombre | le total disparaît |\n",
        encoding="utf-8",
    )

    resultat = depot.joue(
        "--planche", "136", cwd=arbre, env={"PATH": python_sur_le_chemin(tmp_path / "bin")}
    )
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    copie = depot.racine / ".maestro" / "relecture" / "136" / "planche.html"
    assert copie.is_file(), "la planche est recopiée dans le clone principal"
    assert os.path.samefile(chemin_rendu(resultat.stdout, "PLANCHE"), copie), (
        "la dernière ligne nomme la copie qui survit, jamais celle du worktree"
    )
    html = copie.read_text(encoding="utf-8")
    assert "data:image/png;base64," in html
    assert "le total disparaît" in html
    assert html.index("<h2>Jugement") < html.index("État « peuple »"), "le jugement est en tête"
    assert not re.search(r'(?:src|href)="https?://', html), "autonome : aucune ressource externe"


def test_la_planche_nomme_la_capture_qu_elle_ecarte_au_plafond(tmp_path: Path) -> None:
    """Une planche est faite pour être ouverte : au-delà du plafond, une capture est écartée ET
    nommée — jamais tue."""
    depot = DepotRelecture(tmp_path / "depot")
    depot.recopie(PLANCHE_PY, BUILD_PY)
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    depot.capture(".maestro/relecture/137/couts-clair.png", PNG + b"\x00" * 4096)
    resultat = depot.joue(
        "--planche",
        "137",
        env={
            "PATH": python_sur_le_chemin(tmp_path / "bin"),
            "MAESTRO_RELECTURE_PLANCHE_MAX": "0.01",
        },
    )
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
    assert "capture(s) écartée(s)" in resultat.stderr
    html = (depot.racine / ".maestro" / "relecture" / "137" / "planche.html").read_text(
        encoding="utf-8"
    )
    assert "Capture écartée pour tenir le plafond" in html
    assert "data:image/png;base64," not in html


# =================================================================================================
# Le regard neuf, le skill, et les ancres que les commandes posent (#977, #978, #980)
# =================================================================================================


def outils_de_l_agent(texte: str) -> list[str]:
    entete = texte.split("---", 2)[1]
    ligne = re.search(r"^tools:[ \t]*(.*)$", entete, re.M)
    assert ligne, "en-tête sans `tools:` : un agent de projet hériterait de TOUS les outils"
    return [outil.strip() for outil in ligne.group(1).split(",") if outil.strip()]


def test_le_regard_neuf_n_a_que_read() -> None:
    """Savoir comment l'écran est écrit rendrait indulgent pour ce qu'il montre — et c'est l'OUTIL,
    pas la consigne, qui l'empêche de lire le code."""
    texte = AGENT_REGARD.read_text(encoding="utf-8")
    fautif = texte.replace("tools: Read", "tools: Read, Bash, Grep")
    assert fautif != texte and outils_de_l_agent(fautif) == ["Read", "Bash", "Grep"], "motif creux"
    assert outils_de_l_agent(texte) == ["Read"]
    assert re.search(r"^name: regard-neuf$", texte, re.M)


def test_le_skill_confie_le_jugement_par_la_saisine_seule() -> None:
    """Le prompt du sous-agent est une phrase au mot près — un mot de contexte en plus serait ce que
    le geste retire. Et l'ordre est la décision : les pièces, le regard, puis la trace."""
    texte = SKILL.read_text(encoding="utf-8")
    assert "Ta saisine : <chemin de la ligne SAISINE> — lis-la, puis rends-la remplie." in texte
    saisine = texte.index("relecture-visuelle.sh --saisine <iid>")
    regard = texte.index('subagent_type: "regard-neuf"')
    trace = texte.index("lib.sh relecture-note <iid>")
    assert saisine < regard < trace


def test_la_grille_n_est_ecrite_qu_une_fois() -> None:
    """Trois lecteurs, aucun ne la recopie : une question recopiée finirait par diverger.

    ⚠ Un prompt est replié à 100 colonnes : une question recopiée y serait coupée n'importe où, et
    un `in` sur le texte brut la manquerait. L'échantillon fautif le montre avant qu'on balaie.
    """
    questions = [
        ligne.split("\t")[1]
        for ligne in GRILLE.read_text(encoding="utf-8").splitlines()
        if ligne and not ligne.startswith("#")
    ]
    lecteurs = (SKILL, AGENT_REGARD, PROMPT_FINISH, RELECTURE_SH, LIB_SH)
    replie = "\n".join(textwrap.wrap(questions[0], 60))
    fautif = SKILL.read_text(encoding="utf-8") + "\n" + replie + "\n"
    assert questions[0] not in fautif, "l'échantillon n'est pas replié : il ne montrerait rien"
    assert questions[0] in " ".join(fautif.split()), (
        "motif creux : la normalisation ne rattrape rien"
    )
    for lecteur in lecteurs:
        texte = " ".join(lecteur.read_text(encoding="utf-8").split())
        for question in questions:
            assert question not in texte, (
                f"{lecteur.name} recopie une question de la grille — elle vit dans "
                "scripts/design/grille-relecture.tsv, et nulle part ailleurs (#980)"
            )


def test_l_avant_se_capture_sous_le_nom_que_le_script_construit() -> None:
    """Un seul endroit construit le nom d'une capture : le skill doit dire le même, sans quoi la
    saisine, la planche et la couverture chercheraient des fichiers que personne n'a écrits."""
    script = RELECTURE_SH.read_text(encoding="utf-8")
    apres = re.search(r"^nom_capture\(\) \{ printf '([^']+)'", script, re.M)
    avant = re.search(r"^nom_capture_avant\(\) \{ printf '([^']+)'", script, re.M)
    assert apres and avant, "les deux constructeurs de noms ont changé de forme"

    def forme(gabarit: str) -> str:
        return gabarit.replace("%s", "<ecran>", 1).replace("%s", "<theme>", 1)

    skill = SKILL.read_text(encoding="utf-8")
    assert f".maestro/relecture/<iid>/{forme(apres.group(1))}" in skill
    assert f".maestro/relecture/<iid>/{forme(avant.group(1))}" in skill
    assert f".maestro/relecture/<iid>/<etat>/{forme(avant.group(1))}" in skill


def test_le_skill_decrit_chaque_etat_de_la_vraie_stack_et_chaque_non_couvert(
    tmp_path: Path,
) -> None:
    """Le script DÉCLARE les états ; le skill, lui, DIT ce qu'on y cherche. Un état ajouté sans sa
    ligne serait monté et capturé sans que personne sache quoi y regarder — et un non couvert que le
    skill tairait finirait imité (#1165)."""
    depot = DepotRelecture(tmp_path / "depot")
    depot.ecris("apps/web/app/couts/page.tsx", "// en cours\n")
    noms = etats_du_script() + list(non_couverts(depot, "140"))
    assert len(noms) >= 4, "motif creux : aucun état lu dans le script"
    skill = SKILL.read_text(encoding="utf-8")
    for nom in noms:
        assert f"| `{nom}` |" in skill, f"l'état « {nom} » n'est pas décrit par le skill"
    assert "relecture-visuelle.sh <iid> --etat <nom>" in skill
    assert "relecture-visuelle.sh --couverture <iid>" in skill
    assert "--scenario" not in skill and "start.sh --demo" not in skill, "plus un geste de la démo"


def test_la_relecture_ne_lit_plus_la_demo_nulle_part() -> None:
    """Critère de #1165 : ni `demo.py`, ni `--demo`, ni sur la branche ni sur origin/main. Le
    script ne cite la démo que pour dater ce qu'elle faisait ; il ne lit, ne lance ni ne déclare
    rien d'elle. L'échantillon fautif prouve que le balayage voit ce qu'il cherche."""
    motif = re.compile(r"controltower/demo\.py|--demo\b|prj-demo|PROJET_ID|SCENARIO_")
    fautif = (
        "PROJET_DEMO=\"$(sed -n 's/^PROJET_ID *= *\"\\([^\"]*\\)\".*/\\1/p' "
        '"$RACINE/maestro/controltower/demo.py")"'
    )
    assert motif.search(fautif), "motif creux : la ligne d'avant #1165 ne se verrait pas"
    for lecteur in (RELECTURE_SH, RELECTURE_PROJETS):
        trouve = [ligne for ligne in lecteur.read_text(encoding="utf-8").splitlines()
                  if motif.search(ligne)]
        assert trouve == [], f"{lecteur.name} s'appuie encore sur la démo : {trouve}"


def test_les_ancres_lues_par_la_relecture_ont_chacune_leur_ecrivain() -> None:
    """`relecture-attente` tire les décisions par une ancre en TÊTE de commentaire. Une ancre
    renommée d'un seul côté rendrait « aucune décision consignée » sur un ticket qui en porte : le
    regard neuf jugerait l'écran sans la décision qu'il devait confronter."""
    trouve = re.search(
        r'^GL_RELECTURE_DECISIONS="\$\{GL_RELECTURE_DECISIONS:-([^}]+)\}"',
        LIB_SH.read_text(encoding="utf-8"),
        re.M,
    )
    assert trouve, "GL_RELECTURE_DECISIONS introuvable dans lib.sh"
    ancres = trouve.group(1).split("|")
    assert len(ancres) >= 2
    commandes = {
        chemin.name: " ".join(chemin.read_text(encoding="utf-8").split())
        for chemin in (RACINE / ".claude" / "commands").glob("*.md")
    }
    for ancre in ancres:
        ecrivains = [nom for nom, texte in commandes.items() if f"commence par `{ancre}`" in texte]
        assert ecrivains, f"aucune commande n'écrit un commentaire qui commence par « {ancre} »"


def test_la_cloture_dit_le_refus_de_grille_et_nomme_la_planche() -> None:
    etape = etape_4bis()
    assert "`5` jugement sans sa **grille** entière" in etape
    # #1151 : la réparation dépend de qui juge — rejouer le regard neuf pour un ticket qui décide
    # d'un écran, compléter sa propre grille pour tout autre. Jamais la grille retirée.
    assert "rejouant le regard neuf du skill pour un ticket qui décide d'un écran" in etape
    assert "en complétant ta propre grille pour tout autre" in etape
    assert "`PLANCHE <chemin>`" in etape, (
        "la planche est la seule façon pour une personne de VOIR ce que le texte consigné juge"
    )


# =================================================================================================
# Le régime écrit — docs/30 §5.8, et le seul renvoi de CLAUDE.md (#974)
# =================================================================================================

DOC30 = RACINE / "docs" / "30-cible-visuelle-control-tower.md"
CLAUDE_MD = RACINE / "CLAUDE.md"


def section_5_8() -> str:
    texte = DOC30.read_text(encoding="utf-8")
    debut = texte.index("### 5.8 ")
    return texte[debut : texte.index("\n## 6.", debut)]


def test_le_regime_est_ecrit_avec_ce_qui_a_ete_ecarte() -> None:
    """Un régime réduit à sa conduite se rouvre au premier doute. Chaque lot a sa section, et ce qui
    a été écarté y est écrit avec sa raison — la voie (c) de #979 d'abord, qui reviendrait la
    première parce qu'elle est la plus simple."""
    section = section_5_8()
    for lot in ("#976", "#977", "#978", "#979", "#980"):
        assert f"({lot})" in section, f"le lot {lot} n'a pas sa section dans docs/30 §5.8"
    assert section.count("**Écarté") >= 4, "un lot a perdu ce qu'il avait écarté"
    assert "(c) implémenter la variante la plus proche" in section
    assert "**écartée**" in section and "**retenue**" in section


def test_claude_md_n_en_garde_qu_un_renvoi() -> None:
    """CLAUDE.md est chargé par chaque session (#965) : il garde la règle et renvoie à sa
    démonstration, jamais ne la recopie. L'échantillon fautif prouve que la phrase cherchée est
    bien celle de la section."""
    texte = CLAUDE_MD.read_text(encoding="utf-8")
    assert "§5.8" in texte, "CLAUDE.md ne renvoie pas au régime de #972"
    demonstration = "la question se repose-t-elle d'elle-même"
    assert demonstration in " ".join(section_5_8().split()), "motif creux : la section l'a perdue"
    assert demonstration not in " ".join(texte.split())
