"""Tests du filet CI local — `scripts/ci/local.sh` (ticket #156, lot final du parent #155).

Le script rejoue en local les jobs de `.gitlab-ci.yml` avant le push : à plusieurs, découvrir un
échec de lint par le pipeline, c'est le découvrir sur la machine de quelqu'un d'autre et occuper
son runner pour une faute de frappe (docs/10 §8). Un filet qui MENT — vert alors que la CI serait
rouge, ou l'inverse — est pire que pas de filet : ces tests épinglent donc ses verdicts.

Ce qui est vérifié :

* les réglages sont **lus dans `.gitlab-ci.yml`** (sévérité shellcheck, seuil de couverture, image)
  plutôt que recopiés — le filet suit le pipeline quand celui-ci change ;
* **un job non jouable n'est pas un échec** : outil absent ⇒ `IGNORÉ` et verdict annoncé PARTIEL
  (bloquant seulement avec `--strict`) ;
* **un étage lint rouge arrête le pipeline**, comme GitLab ;
* **`web-build` suit la même règle de périmètre** que le pipeline (apps/web modifié ou non) ;
* **shellcheck analyse un miroir en LF** : une copie de travail Windows en CRLF ne doit pas
  inventer des SC1017 que la CI ne verra jamais ;
* **pytest teste le code d'ICI** (#194) : lancé par `python -m` et non par le script console, et
  précédé d'une sonde qui rend le job `IGNORÉ` — jamais rouge — si `import maestro` se résout dans
  un autre répertoire de travail.

**Ni réseau ni vrais outils.** Un dépôt jetable est monté dans `tmp_path`, avec des **shims** en
tête du `PATH` (`shellcheck`, `docker`, `npm`) et de faux exécutables dans son `.venv` / son
`.tools/node`. Chacun journalise ce qu'il reçoit et rend le code qu'on lui demande : ce sont les
DÉCISIONS du script qui sont testées, jamais les outils eux-mêmes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

NODE_PIN = "20.19.0"
SEVERITE = "style"
SEUIL = "77"
IMAGE = "koalaman/shellcheck-alpine:v0.0.0-essai"

# Pipeline synthétique. La ligne de commentaire porte volontairement une AUTRE sévérité : le motif
# de lecture inclut le nom de la commande, c'est ce qui l'empêche d'attraper la mauvaise valeur.
GITLAB_CI = f"""\
stages: [lint, test]

shellcheck:
  stage: lint
  image: {IMAGE}
  script:
    # à durcir en --severity=error quand le dépôt sera propre
    - shellcheck --severity={SEVERITE} $(find scripts -name '*.sh')

pytest:
  stage: test
  script:
    - pytest --cov=maestro --cov-fail-under={SEUIL}
"""

# Shim générique : journalise son appel, imprime ce qu'on lui a demandé d'imprimer, rend le code
# qu'on lui a demandé de rendre. `${{nom^^}}` -> MAESTRO_FAUX_RUFF_CODE, etc.
SHIM = """\
#!/usr/bin/env bash
nom="%(nom)s"
printf '%%s %%s\\n' "$nom" "$*" >> "$MAESTRO_FAUX_JOURNAL"
majuscule="${nom^^}"
sortie="MAESTRO_FAUX_${majuscule}_SORTIE"
code="MAESTRO_FAUX_${majuscule}_CODE"
printf '%%b' "${!sortie:-}"
exit "${!code:-0}"
"""

# Le python du venv sert à TROIS choses dans `job_pytest` : la SONDE qui dit où `import maestro` se
# résout (« python - », script sur stdin), la disponibilité de pytest-xdist (« python -c import
# xdist », #214), puis pytest lui-même (« python -m pytest »). Un seul shim à trois branches —
# c'est ce qui permet de rejouer un worktree dont le venv partagé importe le `maestro` d'un AUTRE
# répertoire de travail (#194), ou un venv d'avant #214, sans venv ni worktree réels.
SHIM_PYTHON = """\
#!/usr/bin/env bash
printf 'python %s\\n' "$*" >> "$MAESTRO_FAUX_JOURNAL"
case " $* " in
  *"-c import xdist"*)
    exit "${MAESTRO_FAUX_XDIST_CODE:-0}"
    ;;
  *" -m pytest "*)
    printf '%b' "${MAESTRO_FAUX_PYTEST_SORTIE:-}"
    exit "${MAESTRO_FAUX_PYTEST_CODE:-0}"
    ;;
esac
cat >/dev/null                       # la sonde arrive par stdin : on la draine sans la lire
printf '%b' "${MAESTRO_FAUX_SONDE_SORTIE:-ICI\\n}"
exit "${MAESTRO_FAUX_SONDE_CODE:-0}"
"""

# Le dépôt jetable a la FORME du vrai, en miniature : c'est ce qui rend le périmètre de pytest
# (#214) observable. Une suite d'outillage n'y est pas déclarée comme telle — elle NOMME un script
# du dépôt, exactement comme les vraies (`tests/test_orchestrate.py` cite `run.sh`), et c'est de
# là que le script la déduit.
ARBORESCENCE = {
    "maestro/__init__.py": "",
    "maestro/moteur.py": "def tourne() -> None: ...\n",
    "tests/conftest.py": "# garde-fous communs\n",
    "tests/test_moteur.py": "from maestro.moteur import tourne\n",
    "tests/test_horloge.py": "# une suite applicative qui ne cite aucun script\n",
    "tests/test_outillage.py": '"""Pilote scripts/gitlab/lib.sh dans un dépôt jetable."""\n',
    "scripts/gitlab/lib.sh": "#!/usr/bin/env bash\necho lib\n",
    # Une suite qui relit tout un répertoire le désigne par son NOM, jamais par celui de ses
    # fichiers — comme test_collaboration avec `.claude/commands/*.md` (#196).
    "tests/test_prompts.py": '"""Relit les prompts de .claude/commands/."""\n',
    ".claude/commands/ticket-start.md": "# /ticket-start\n",
    "docs/10-workflow-git.md": "# Workflow\n",
    ".mcp.json": "{}\n",
    # Comme dans le vrai dépôt : les artefacts posés par `equipe_tout` (venv, Node vendoré,
    # node_modules) sont ignorés de git. Sans ça ils compteraient comme du travail non commité et
    # ramèneraient tout diff au périmètre maximal — le filet serait juste, mais pour de mauvaises
    # raisons, et ces tests ne prouveraient plus rien.
    ".gitignore": ".venv/\n.tools/\nnode_modules/\n",
}

# shellcheck a son propre shim : il doit pouvoir REFUSER un fichier à retour chariot, comme le
# vrai (SC1017). C'est ce qui permet de vérifier que le script lui présente un miroir en LF.
SHIM_SHELLCHECK = """\
#!/usr/bin/env bash
printf 'shellcheck %s\\n' "$*" >> "$MAESTRO_FAUX_JOURNAL"
for fichier in "$@"; do
  case "$fichier" in --*) continue ;; esac
  [ -f "$fichier" ] || continue
  if grep -q $'\\r' "$fichier"; then
    printf 'In %s line 1:\\nSC1017 (error): Literal carriage return.\\n' "$fichier"
    exit 1
  fi
done
printf '%b' "${MAESTRO_FAUX_SHELLCHECK_SORTIE:-}"
exit "${MAESTRO_FAUX_SHELLCHECK_CODE:-0}"
"""


@dataclass
class Clone:
    """Clone jetable : le vrai `ci/local.sh`, un pipeline synthétique et des outils factices."""

    racine: Path
    fauxbin: Path
    journal: Path
    tmp: Path

    # --- équipement à la carte ---
    def pose_shim(self, nom: str, dossier: Path | None = None, corps: str | None = None) -> None:
        cible = (dossier or self.fauxbin) / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(corps or SHIM % {"nom": Path(nom).stem}, encoding="utf-8", newline="\n")
        cible.chmod(0o755)

    def pose_outil_venv(self, nom: str, corps: str | None = None) -> None:
        """Un outil du venv du dépôt, dans les deux dispositions (Windows et Unix)."""
        self.pose_shim(f"{nom}.exe", self.racine / ".venv" / "Scripts", corps)
        self.pose_shim(nom, self.racine / ".venv" / "bin", corps)

    def pose_node(self) -> None:
        """Le Node vendoré et son npm, dans les deux dispositions."""
        base = self.racine / ".tools" / "node" / f"v{NODE_PIN}"
        self.pose_shim("node.exe", base)
        self.pose_shim("npm", base)
        self.pose_shim("node", base / "bin")
        self.pose_shim("npm", base / "bin")
        (self.racine / "apps" / "web" / "node_modules").mkdir(parents=True, exist_ok=True)

    def equipe_tout(self) -> None:
        self.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)
        for outil in ("ruff", "pytest", "mypy"):
            self.pose_outil_venv(outil)
        self.pose_outil_venv("python", corps=SHIM_PYTHON)
        self.pose_node()

    # --- exécution ---
    def lance(self, *args: str, **reglages: str) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        environnement.update(
            {
                "PATH": os.pathsep.join([str(self.fauxbin), environnement.get("PATH", "")]),
                "TMPDIR": str(self.tmp),
                "MAESTRO_FAUX_JOURNAL": str(self.journal),
            }
        )
        environnement.update(reglages)
        assert BASH is not None
        return subprocess.run(  # noqa: S603
            [BASH, str(self.racine / "scripts" / "ci" / "local.sh"), *args],
            cwd=str(self.racine),
            env=environnement,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

    def modifie(self, chemin: str, contenu: str = "# modifié\n") -> None:
        """Un changement NON COMMITÉ — ce qui partira au push, donc ce que le périmètre regarde."""
        cible = self.racine / chemin
        cible.parent.mkdir(parents=True, exist_ok=True)
        with cible.open("a", encoding="utf-8", newline="\n") as fichier:
            fichier.write(contenu)

    def appels(self) -> list[str]:
        if not self.journal.exists():
            return []
        return [ligne for ligne in self.journal.read_text(encoding="utf-8").splitlines() if ligne]

    def git(self, *args: str) -> None:
        assert GIT is not None
        subprocess.run(  # noqa: S603
            [GIT, "-c", "core.hooksPath=", *args],
            cwd=str(self.racine),
            check=True,
            capture_output=True,
        )


def lancements_pytest(appels: list[str]) -> list[str]:
    """Les appels qui ont RÉELLEMENT joué la suite — `python -m pytest`, jamais la sonde."""
    return [appel for appel in appels if "-m pytest" in appel]


def ligne_du_job(sortie: str, job: str) -> str:
    """La ligne du RÉSUMÉ concernant ce job (statut + détail)."""
    resume = sortie[sortie.rindex("Résumé"):]
    for ligne in resume.splitlines():
        if f" {job} " in ligne:
            return ligne
    raise AssertionError(f"job {job!r} absent du résumé :\n{sortie}")


@pytest.fixture
def clone(tmp_path: Path) -> Clone:
    assert GIT is not None
    racine = tmp_path / "clone"
    origin = tmp_path / "origin.git"
    fauxbin = tmp_path / "fauxbin"
    tmp = tmp_path / "tmp"
    for dossier in (fauxbin, tmp):
        dossier.mkdir()

    def git(*args: str, cwd: Path) -> None:
        subprocess.run(  # noqa: S603
            [GIT, "-c", "core.hooksPath=", *args], cwd=str(cwd), check=True, capture_output=True
        )

    origin.mkdir()
    git("init", "--bare", "--quiet", "--initial-branch=main", cwd=origin)

    (racine / "scripts" / "ci").mkdir(parents=True)
    shutil.copy2(RACINE / "scripts" / "ci" / "local.sh", racine / "scripts" / "ci" / "local.sh")
    (racine / ".gitlab-ci.yml").write_text(GITLAB_CI, encoding="utf-8", newline="\n")
    (racine / ".node-version").write_text(f"v{NODE_PIN}\n", encoding="utf-8", newline="\n")
    (racine / "apps" / "web").mkdir(parents=True)
    (racine / "apps" / "web" / "package.json").write_text("{}\n", encoding="utf-8", newline="\n")
    for chemin, contenu in ARBORESCENCE.items():
        cible = racine / chemin
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu, encoding="utf-8", newline="\n")

    git("init", "--quiet", "--initial-branch=main", cwd=racine)
    git("config", "user.email", "test@maestro.invalid", cwd=racine)
    git("config", "user.name", "Maestro Test", cwd=racine)
    git("add", "-A", cwd=racine)
    git("commit", "--quiet", "-m", "chore: dépôt jetable", cwd=racine)
    git("remote", "add", "origin", str(origin), cwd=racine)
    git("push", "--quiet", "-u", "origin", "main", cwd=racine)

    clone = Clone(racine=racine, fauxbin=fauxbin, journal=tmp_path / "outils.log", tmp=tmp)
    # `docker` est systématiquement neutralisé : aucun test ne doit toucher au Docker du poste.
    clone.pose_shim("docker", corps="#!/usr/bin/env bash\nexit 1\n")
    return clone


# --- Les réglages viennent du pipeline ------------------------------------------------------------


def test_list_reprend_les_reglages_de_gitlab_ci(clone: Clone) -> None:
    """Seuil et sévérité sont LUS dans `.gitlab-ci.yml` : le filet suit le pipeline.

    Le seuil se lit en « --complet » : c'est le seul mode qui l'applique (#214), le mode rapide
    jouant un sous-ensemble qui ne peut pas le tenir.
    """
    acheve = clone.lance("--list")
    assert acheve.returncode == 0, acheve.stderr
    assert f"--severity={SEVERITE}" in acheve.stdout
    assert f"--cov-fail-under={SEUIL}" in clone.lance("--complet", "--list").stdout
    # La sévérité citée dans un COMMENTAIRE du pipeline ne doit pas l'emporter.
    assert "--severity=error" not in acheve.stdout
    for job in ("shellcheck", "python-lint", "pytest", "mypy", "web-build"):
        assert job in acheve.stdout


def test_un_nom_de_job_inconnu_est_refuse_tout_de_suite(clone: Clone) -> None:
    """Une faute de frappe ne doit pas rendre un « tout vert » qui n'a rien joué."""
    acheve = clone.lance("--only", "pytest,pyteste")
    assert acheve.returncode == 2
    assert "Job inconnu : pyteste" in acheve.stderr


def test_les_alias_d_etage_se_developpent(clone: Clone) -> None:
    clone.equipe_tout()
    acheve = clone.lance("--only", "lint")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "OK" in ligne_du_job(acheve.stdout, "shellcheck")
    assert "SAUTÉ" in ligne_du_job(acheve.stdout, "pytest")


# --- Verdicts ------------------------------------------------------------------------------------


def test_tout_vert_quand_les_jobs_passent(clone: Clone) -> None:
    clone.equipe_tout()
    # « --complet » : ces tests-ci portent sur les VERDICTS, pas sur le périmètre (#214). Le mode
    # par défaut ne jouerait rien ici — le dépôt jetable est propre, donc aucun test n'est concerné.
    acheve = clone.lance(
        "--complet",
        MAESTRO_FAUX_PYTEST_SORTIE="TOTAL 100 0 95%\\n12 passed in 1.2s\\n",
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Verdict : VERT" in acheve.stdout
    assert "(partiel)" not in acheve.stdout
    # Le détail du job pytest reprend le décompte ET la couverture, seuil à l'appui.
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "12 passed" in ligne and "95%" in ligne and f"seuil {SEUIL}" in ligne


def test_un_job_rouge_rend_un_verdict_rouge(clone: Clone) -> None:
    clone.equipe_tout()
    acheve = clone.lance(
        "--complet", "--only", "pytest",
        MAESTRO_FAUX_PYTEST_CODE="1",
        MAESTRO_FAUX_PYTEST_SORTIE="TOTAL 100 20 80%\\n2 failed, 10 passed in 1.2s\\n",
    )
    assert acheve.returncode == 1
    assert "Verdict : ÉCHEC" in acheve.stdout
    assert "2 failed" in ligne_du_job(acheve.stdout, "pytest")


def test_un_etage_lint_rouge_arrete_le_pipeline_comme_gitlab(clone: Clone) -> None:
    clone.equipe_tout()
    acheve = clone.lance(
        MAESTRO_FAUX_RUFF_CODE="1",
        MAESTRO_FAUX_RUFF_SORTIE="Found 3 errors.\\n",
    )
    assert acheve.returncode == 1
    assert "3 errors" in ligne_du_job(acheve.stdout, "python-lint")
    assert "NON JOUÉ" in ligne_du_job(acheve.stdout, "pytest")
    assert "étage lint en échec" in acheve.stdout
    # …et l'étage test n'a effectivement pas tourné.
    assert lancements_pytest(clone.appels()) == []


def test_un_outil_absent_est_ignore_pas_compte_en_echec(clone: Clone) -> None:
    """« Mieux vaut un verdict honnêtement incomplet qu'un faux vert. »"""
    clone.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)     # le venv, lui, reste absent
    acheve = clone.lance("--only", "lint")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Verdict : VERT (partiel)" in acheve.stdout
    ligne = ligne_du_job(acheve.stdout, "python-lint")
    assert "IGNORÉ" in ligne
    assert "scripts/setup.sh --only venv" in ligne


def test_strict_rend_bloquant_un_job_non_joue(clone: Clone) -> None:
    clone.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)
    acheve = clone.lance("--only", "lint", "--strict")
    assert acheve.returncode == 1
    assert "Verdict : VERT (partiel)" in acheve.stdout


def test_ne_dit_jamais_vert_quand_rien_n_a_tourne(clone: Clone) -> None:
    clone.equipe_tout()
    acheve = clone.lance("--skip", "lint,test")
    assert acheve.returncode == 0
    assert "Verdict : AUCUN JOB JOUÉ" in acheve.stdout
    assert clone.appels() == []


# --- Le code testé est celui d'ICI (#194) ---------------------------------------------------------


def test_pytest_est_lance_par_python_m_et_non_par_le_script_console(clone: Clone) -> None:
    """Régression #194 : le script console ne met pas le répertoire courant dans `sys.path`.

    Dans un worktree, le `.venv` est partagé avec le clone principal et y installe `maestro` en
    éditable **pointé sur celui-ci** (docs/10 §9). Lancé par `pytest.exe`, le job jouait donc les
    tests d'ici contre le code de LÀ-BAS, pendant que `--cov=maestro` — un chemin, lui, relatif au
    répertoire courant — instrumentait la copie d'ici : couverture 0 %, faux rouge. Et le faux vert
    symétrique, plus grave, ne se voyait pas. `python -m` remet le répertoire courant en tête.
    """
    clone.equipe_tout()
    acheve = clone.lance(
        "--complet", "--only", "pytest",
        MAESTRO_FAUX_PYTEST_SORTIE="TOTAL 100 0 95%\\n12 passed in 1.2s\\n",
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    lances = lancements_pytest(clone.appels())
    assert len(lances) == 1, clone.appels()
    assert lances[0].startswith("python -m pytest ")
    assert f"--cov=maestro --cov-fail-under={SEUIL}" in lances[0]
    # Le script console n'est jamais appelé — c'est lui qui résolvait `maestro` ailleurs.
    assert not any(appel.startswith("pytest ") for appel in clone.appels())
    # `--list` annonce la commande réellement jouée, pas celle du pipeline.
    assert "python -m pytest" in clone.lance("--list").stdout


def test_pytest_ignore_quand_maestro_se_resout_ailleurs(clone: Clone) -> None:
    """Un job qui ne testerait pas le code d'ici ne rend NI vert NI rouge : il se déclare IGNORÉ.

    C'est le filet du filet : si le mécanisme de #194 revenait par une autre porte, le verdict
    dirait pourquoi il ne vaut rien, au lieu de rendre un rouge faux (ou un vert faux).
    """
    clone.equipe_tout()
    acheve = clone.lance(
        "--only", "pytest",
        MAESTRO_FAUX_SONDE_SORTIE="AILLEURS /ailleurs/Maestro/maestro\\n",
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "IGNORÉ" in ligne
    assert "/ailleurs/Maestro/maestro" in ligne     # la raison NOMME le paquet qui aurait été testé
    assert "docs/10 §9" in ligne
    # Jamais un rouge : le verdict est partiel…
    assert "Verdict : VERT (partiel)" in acheve.stdout
    assert "Verdict : ÉCHEC" not in acheve.stdout
    # …et la suite n'a pas tourné pour rien (près de 20 min pour un verdict sans valeur).
    assert lancements_pytest(clone.appels()) == []


def test_pytest_ignore_quand_la_sonde_est_muette(clone: Clone) -> None:
    """Sonde sans réponse : on ignore quel code serait testé, donc le verdict serait sans valeur."""
    clone.equipe_tout()
    acheve = clone.lance(
        "--only", "pytest",
        MAESTRO_FAUX_SONDE_SORTIE="\\n",
        MAESTRO_FAUX_SONDE_CODE="1",
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "IGNORÉ" in ligne
    assert lancements_pytest(clone.appels()) == []


def test_pytest_absent_du_venv_reste_ignore_et_non_rouge(clone: Clone) -> None:
    """`python -m pytest` sur un venv sans pytest sortirait en 1 : ce serait un faux rouge.

    D'où le contrôle de présence maintenu sur le SCRIPT CONSOLE, qui reste le marqueur
    d'installation — même si ce n'est plus lui qu'on lance.
    """
    clone.equipe_tout()
    for chemin in ("Scripts/pytest.exe", "bin/pytest"):
        (clone.racine / ".venv" / chemin).unlink()
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "IGNORÉ" in ligne
    assert "scripts/setup.sh --only venv" in ligne
    assert lancements_pytest(clone.appels()) == []


# --- Périmètre de pytest (#214) -------------------------------------------------------------------
# La suite complète coûte 9 min 57 s en série, dont 9 pour les ~360 tests d'outillage. Le filet ne
# joue donc par défaut que les suites que le diff concerne, la suite entière restant celle du
# pipeline de la MR (docs/10 §8). Un périmètre qui se trompe est pire que pas de périmètre : ces
# tests épinglent la règle, et surtout son sens de dérive — dans le doute, on élargit.


def suites_jouees(appels: list[str]) -> list[str]:
    """Les fichiers de tests passés à pytest — vide quand il collecte toute la suite lui-même."""
    lances = lancements_pytest(appels)
    assert len(lances) == 1, appels
    return [mot for mot in lances[0].split() if mot.startswith("tests/")]


def test_une_modification_de_maestro_ne_joue_pas_l_outillage(clone: Clone) -> None:
    """Le cas courant : on écrit du code applicatif, les tests qui pilotent des scripts n'ont rien
    de neuf à dire — et ce sont eux qui coûtent 9 des 10 minutes."""
    clone.equipe_tout()
    clone.modifie("maestro/moteur.py")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    jouees = suites_jouees(clone.appels())
    assert "tests/test_moteur.py" in jouees
    # Une suite qui ne cite aucun script est APPLICATIVE par défaut : jamais sautée en silence.
    assert "tests/test_horloge.py" in jouees
    assert "tests/test_outillage.py" not in jouees
    assert "maestro/** modifié" in ligne_du_job(acheve.stdout, "pytest")


def test_une_modification_de_script_ne_joue_que_les_suites_qui_le_nomment(clone: Clone) -> None:
    clone.equipe_tout()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert suites_jouees(clone.appels()) == ["tests/test_outillage.py"]
    assert "lib.sh" in ligne_du_job(acheve.stdout, "pytest")


def test_un_fichier_transverse_ramene_la_suite_entiere(clone: Clone) -> None:
    """`conftest.py` vaut pour toute la suite : son périmètre, c'est tout."""
    clone.equipe_tout()
    clone.modifie("tests/conftest.py")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    # Aucun fichier passé : pytest collecte tout lui-même.
    assert suites_jouees(clone.appels()) == []
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "toute la suite" in ligne and "transverse" in ligne


def test_un_fichier_anonyme_est_rattrape_par_le_nom_de_son_dossier(clone: Clone) -> None:
    """Repli du nom de fichier vers le nom du dossier : sans lui, toucher un prompt de
    `.claude/commands/` rejouerait les 1100 tests — aucune suite ne cite un prompt par son nom,
    elles parcourent le répertoire."""
    clone.equipe_tout()
    clone.modifie(".claude/commands/ticket-start.md", "une ligne de plus\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert suites_jouees(clone.appels()) == ["tests/test_prompts.py"]
    assert "commands/" in ligne_du_job(acheve.stdout, "pytest")


def test_un_fichier_que_personne_ne_nomme_elargit_au_lieu_de_sauter(clone: Clone) -> None:
    """Le sens de dérive du filet : ce qu'il ne sait pas classer, il le paye — il ne le saute
    pas."""
    clone.equipe_tout()
    clone.modifie(".mcp.json", '{"serveurs": {}}\n')
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert suites_jouees(clone.appels()) == []
    assert "aucune suite ne nomme .mcp.json" in ligne_du_job(acheve.stdout, "pytest")


def test_la_prose_ne_declenche_aucune_suite(clone: Clone) -> None:
    """Aucun test ne lit docs/ : jouer 1100 tests pour une phrase de doc serait absurde."""
    clone.equipe_tout()
    clone.modifie("docs/10-workflow-git.md", "une phrase de plus\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert lancements_pytest(clone.appels()) == []
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "HORS PÉRIM." in ligne
    assert "aucune suite concernée" in ligne


def test_le_mode_rapide_n_impose_pas_le_seuil_de_couverture(clone: Clone) -> None:
    """Un sous-ensemble ne peut pas tenir le seuil : l'exiger rendrait un rouge mensonger."""
    clone.equipe_tout()
    clone.modifie("maestro/moteur.py")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "--cov-fail-under" not in lancements_pytest(clone.appels())[0]
    # Et le verdict le dit, plutôt que de laisser croire à un vert qui vaut celui du pipeline.
    assert "Périmètre réduit" in acheve.stdout


def test_le_parallelisme_est_reserve_aux_suites_qui_le_rentabilisent(clone: Clone) -> None:
    """`-n auto` coûte ~5,5 s de démarrage des workers, pour 1,5 s de suite applicative en série.

    Il ne se justifie que sur les suites d'outillage, bornées par les processus qu'elles attendent
    (mesuré : 9 min 57 s → 2 min 34 s sur la suite entière).
    """
    clone.equipe_tout()
    clone.modifie("maestro/moteur.py")
    clone.lance("--only", "pytest")
    assert "-n auto" not in lancements_pytest(clone.appels())[0]

    clone.journal.unlink()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    clone.lance("--only", "pytest")
    assert "-n auto" in lancements_pytest(clone.appels())[0]


def test_sans_pytest_xdist_le_filet_joue_en_serie_au_lieu_de_rougir(clone: Clone) -> None:
    """Le venv d'un clone antérieur à #214 n'a pas xdist : `-n auto` y sortirait en erreur
    d'arguments, un rouge qui ne parle pas du code. Le filet n'installe rien — il s'en passe."""
    clone.equipe_tout()
    # Un périmètre d'outillage : c'est là que `-n auto` serait passé si xdist était disponible.
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest", MAESTRO_FAUX_XDIST_CODE="1")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    lance = lancements_pytest(clone.appels())[0]
    assert "-n auto" not in lance
    assert suites_jouees(clone.appels()) == ["tests/test_outillage.py"]
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "en série" in ligne and "setup.sh --only venv" in ligne


def test_complet_rejoue_toute_la_suite_avec_sa_couverture(clone: Clone) -> None:
    """L'échappatoire quand on veut le verdict du pipeline sans attendre le pipeline."""
    clone.equipe_tout()
    clone.modifie("maestro/moteur.py")
    acheve = clone.lance("--complet", "--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    lance = lancements_pytest(clone.appels())[0]
    assert suites_jouees(clone.appels()) == []
    assert f"--cov=maestro --cov-fail-under={SEUIL}" in lance
    assert "-n auto" in lance
    assert "Périmètre réduit" not in acheve.stdout


# --- Périmètre de web-build -----------------------------------------------------------------------


def test_web_build_hors_perimetre_quand_apps_web_n_a_pas_bouge(clone: Clone) -> None:
    clone.equipe_tout()
    acheve = clone.lance("--skip", "shellcheck,python-lint,pytest,mypy")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    ligne = ligne_du_job(acheve.stdout, "web-build")
    assert "HORS PÉRIM." in ligne
    assert "le pipeline ne le joue pas non plus" in ligne
    assert not any(a.startswith("npm ") for a in clone.appels())


def test_web_build_joue_sur_une_modification_non_commitee(clone: Clone) -> None:
    """C'est ce qui partira au push : le travail non commité compte dans le périmètre."""
    clone.equipe_tout()
    (clone.racine / "apps" / "web" / "page.tsx").write_text(
        "export default function Page() { return null }\n", encoding="utf-8", newline="\n"
    )
    acheve = clone.lance("--skip", "shellcheck,python-lint,pytest,mypy")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    # Le libellé du job énumère les étapes jouées, et #124 y a intercalé vitest entre eslint et
    # next build : on vise les deux bornes plutôt que la phrase entière, pour que l'ajout d'une
    # étape ne casse plus un test qui porte, lui, sur le PÉRIMÈTRE (le travail non commité compte).
    ligne = ligne_du_job(acheve.stdout, "web-build")
    assert "eslint" in ligne and "next build verts" in ligne
    lances = [a for a in clone.appels() if a.startswith("npm ")]
    assert any("run lint" in a for a in lances)
    assert any("test" in a for a in lances)
    assert any("run build" in a for a in lances)


def test_only_web_build_force_le_job_hors_perimetre(clone: Clone) -> None:
    clone.equipe_tout()
    acheve = clone.lance("--only", "web-build")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "OK" in ligne_du_job(acheve.stdout, "web-build")


def test_web_build_ignore_quand_le_node_du_depot_manque(clone: Clone) -> None:
    """Le node du système est hors sujet : c'est la version épinglée du dépôt qui compte (#153)."""
    clone.equipe_tout()
    shutil.rmtree(clone.racine / ".tools")
    acheve = clone.lance("--only", "web-build")
    ligne = ligne_du_job(acheve.stdout, "web-build")
    assert "IGNORÉ" in ligne
    assert "scripts/setup.sh --only node" in ligne


# --- Le miroir en LF de shellcheck ----------------------------------------------------------------


def test_shellcheck_analyse_un_miroir_en_lf(clone: Clone) -> None:
    """Régression : une copie de travail Windows en CRLF inventerait des SC1017 absents de la CI."""
    (clone.racine / "scripts" / "avec-crlf.sh").write_bytes(
        b"#!/usr/bin/env bash\r\necho bonjour\r\n"
    )
    clone.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)
    acheve = clone.lance("--only", "shellcheck")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "SC1017" not in acheve.stdout
    assert "rien à signaler" in ligne_du_job(acheve.stdout, "shellcheck")
    # Les chemins passés restent ceux du dépôt : la sortie désigne les bons fichiers.
    appel = next(a for a in clone.appels() if a.startswith("shellcheck "))
    assert "scripts/avec-crlf.sh" in appel
    assert f"--severity={SEVERITE}" in appel


def test_shellcheck_compte_les_remarques_de_la_severite_lue(clone: Clone) -> None:
    clone.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)
    acheve = clone.lance(
        "--only", "shellcheck",
        MAESTRO_FAUX_SHELLCHECK_CODE="1",
        MAESTRO_FAUX_SHELLCHECK_SORTIE=(
            "In scripts/ci/local.sh line 3:\\nSC2086 (info): Double quote.\\n"
            "In scripts/ci/local.sh line 9:\\nSC2181 (style): Check exit code.\\n"
            "For more information:\\n  https://www.shellcheck.net/wiki/SC2086 -- Double quote\\n"
        ),
    )
    assert acheve.returncode == 1
    ligne = ligne_du_job(acheve.stdout, "shellcheck")
    # Deux remarques, pas trois : le pied de page « SCxxxx -- » n'est pas recompté.
    assert "2 remarque(s)" in ligne
    assert f"sévérité {SEVERITE}+" in ligne


@pytest.mark.skipif(
    shutil.which("shellcheck") is not None,
    reason="un vrai shellcheck est sur le PATH : le cas « absent » n'est pas reproductible ici",
)
def test_shellcheck_absent_renvoie_a_l_image_du_pipeline(clone: Clone) -> None:
    """Le filet n'installe ni ne télécharge rien : il dit ce qui manque et comment l'obtenir."""
    acheve = clone.lance("--only", "shellcheck")
    ligne = ligne_du_job(acheve.stdout, "shellcheck")
    assert "IGNORÉ" in ligne
    assert IMAGE in ligne          # l'image vient bien de .gitlab-ci.yml
