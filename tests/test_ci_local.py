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
  un autre répertoire de travail ;
* **le journal d'un job rouge se lit là où on travaille** (#234, docs/10 §8.5) : sous
  `<racine>/.maestro/ci-local/`, cité en chemin **relatif**, table rase à chaque lancement — un
  chemin absolu hors du worktree met la raison de l'échec hors de portée d'une session autonome ;
* **le typage web est jouable par `npm run typecheck`** (#236), et le filet le joue avant vitest.

**Ni réseau ni vrais outils.** Un dépôt jetable est monté dans `tmp_path`, avec des **shims** en
tête du `PATH` (`shellcheck`, `docker`, `npm`) et de faux exécutables dans son `.venv` / son
`.tools/node`. Chacun journalise ce qu'il reçoit et rend le code qu'on lui demande : ce sont les
DÉCISIONS du script qui sont testées, jamais les outils eux-mêmes.
"""

from __future__ import annotations

import json
import os
import re
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
#
# `MAESTRO_FAUX_SHELLCHECK_CIBLE` restreint la sortie dictée à UN fichier. Depuis #285 le filet
# appelle shellcheck une fois par fichier : un shim qui répéterait sa sortie à chaque appel
# compterait N fois la même remarque, et le test ne dirait plus rien du décompte.
SHIM_SHELLCHECK = """\
#!/usr/bin/env bash
printf 'shellcheck %s\\n' "$*" >> "$MAESTRO_FAUX_JOURNAL"
for fichier in "$@"; do
  case "$fichier" in -*) continue ;; esac
  [ -f "$fichier" ] || continue
  if grep -q $'\\r' "$fichier"; then
    printf 'In %s line 1:\\nSC1017 (error): Literal carriage return.\\n' "$fichier"
    exit 1
  fi
done
cible="${MAESTRO_FAUX_SHELLCHECK_CIBLE:-}"
if [ -n "$cible" ]; then
  case " $* " in *" $cible "*) ;; *) exit 0 ;; esac
fi
printf '%b' "${MAESTRO_FAUX_SHELLCHECK_SORTIE:-}"
exit "${MAESTRO_FAUX_SHELLCHECK_CODE:-0}"
"""

# `docker` qui accepte de jouer : `image inspect` trouve l'image, `run` journalise sa ligne
# complète. C'est ce qui permet de vérifier que le repli ne démarre QU'UN conteneur (#285) —
# l'invariant qui rend le découpage par fichier payant plutôt que ruineux (~2 s par conteneur).
# Les sauts de ligne de la boucle sont aplatis : le journal se relit LIGNE PAR LIGNE, et un appel
# qui s'y étale sur cinq lignes ne serait plus reconnaissable comme un seul `docker run`.
SHIM_DOCKER = """\
#!/usr/bin/env bash
printf 'docker %s\\n' "${*//$'\\n'/ }" >> "$MAESTRO_FAUX_JOURNAL"
[ "$1" = run ] || exit 0
printf '%b' "${MAESTRO_FAUX_DOCKER_SORTIE:-}"
exit "${MAESTRO_FAUX_DOCKER_CODE:-0}"
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


def workers_pytest(lance: str) -> int | None:
    """Le nombre de workers passé à pytest-xdist, ou None quand la suite tourne en série.

    Depuis #285 le filet plafonne au lieu de demander `-n auto` : la valeur dépend de la machine
    (min(cœurs, plafond)), c'est donc le CONTRAT qui se teste, jamais un nombre en dur.
    """
    trouve = re.search(r"-n (\S+)", lance)
    if trouve is None:
        return None
    assert trouve.group(1) != "auto", "`-n auto` sur-souscrit la mémoire (#285)"
    return int(trouve.group(1))


def appels_shellcheck(appels: list[str]) -> list[str]:
    return [appel for appel in appels if appel.startswith("shellcheck ")]


def fichiers_de_l_appel(appel: str) -> list[str]:
    """Les fichiers analysés par un appel — ses arguments qui ne sont pas des options."""
    return [mot for mot in appel.split()[1:] if not mot.startswith("-")]


def scripts_du_clone(clone: Clone) -> list[str]:
    """Les scripts à analyser — `scripts/ci/local.sh` compris : le clone jetable en a une copie."""
    return sorted(
        chemin.relative_to(clone.racine).as_posix()
        for chemin in (clone.racine / "scripts").rglob("*.sh")
    )


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
    assert workers_pytest(lancements_pytest(clone.appels())[0]) is None

    clone.journal.unlink()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    clone.lance("--only", "pytest")
    assert workers_pytest(lancements_pytest(clone.appels())[0]) is not None


def test_sans_pytest_xdist_le_filet_joue_en_serie_au_lieu_de_rougir(clone: Clone) -> None:
    """Le venv d'un clone antérieur à #214 n'a pas xdist : `-n` y sortirait en erreur
    d'arguments, un rouge qui ne parle pas du code. Le filet n'installe rien — il s'en passe."""
    clone.equipe_tout()
    # Un périmètre d'outillage : c'est là que `-n` serait passé si xdist était disponible.
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest", MAESTRO_FAUX_XDIST_CODE="1")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    lance = lancements_pytest(clone.appels())[0]
    assert workers_pytest(lance) is None
    assert suites_jouees(clone.appels()) == ["tests/test_outillage.py"]
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "en série" in ligne
    # Le remède n'est plus accroché à ce job-là : il a rejoint le bloc « Dépendances en retard »
    # (#216), qui couvre tout ce que le dépôt a ajouté depuis, pas seulement pytest-xdist.
    assert "Dépendances en retard" in acheve.stdout
    assert "setup.sh --only venv" in acheve.stdout


def test_complet_rejoue_toute_la_suite_avec_sa_couverture(clone: Clone) -> None:
    """L'échappatoire quand on veut le verdict du pipeline sans attendre le pipeline."""
    clone.equipe_tout()
    clone.modifie("maestro/moteur.py")
    acheve = clone.lance("--complet", "--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    lance = lancements_pytest(clone.appels())[0]
    assert suites_jouees(clone.appels()) == []
    assert f"--cov=maestro --cov-fail-under={SEUIL}" in lance
    assert workers_pytest(lance) is not None
    assert "Périmètre réduit" not in acheve.stdout


# --- Le plafond de workers pytest (#285) ----------------------------------------------------------
# `-n auto` demande un worker par cœur logique — 16 sur le poste de mesure. La contrainte n'est pas
# le CPU mais la MÉMOIRE : ~130 Mo par worker, soit ~2 Go à seize pour ~1,8 Go de RAM libre. Le
# poste pagine, et `-n auto` et `-n 8` finissent à égalité (74 s sur tests/test_worktree.py) pour
# deux fois plus de processus. Ces tests épinglent le CONTRAT — jamais plus que le plafond, jamais
# plus que les cœurs — et non un nombre, qui dépend de la machine qui les joue.


def test_les_workers_pytest_sont_plafonnes(clone: Clone) -> None:
    """Le plafond s'applique là où le parallélisme est demandé : un périmètre d'outillage."""
    clone.equipe_tout()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    workers = workers_pytest(lancements_pytest(clone.appels())[0])
    assert workers is not None and 1 <= workers <= 8, f"workers hors plafond : {workers}"


def test_le_plafond_de_workers_ne_depasse_jamais_le_nombre_de_coeurs(clone: Clone) -> None:
    """Un plafond, pas un forçage : sur une machine à 4 cœurs, `-n 4` — ce que `-n auto` donnait.

    C'est ce qui permet de le poser sans distinguer les plateformes : là où les cœurs manquent, le
    réglage est un no-op, et un petit runner ne se retrouve jamais avec plus de workers qu'avant.
    """
    clone.equipe_tout()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest", MAESTRO_PYTEST_WORKERS="512")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    workers = workers_pytest(lancements_pytest(clone.appels())[0])
    assert workers is not None and workers <= (os.cpu_count() or 1), \
        f"{workers} workers demandés pour {os.cpu_count()} cœur(s)"


def test_le_plafond_de_workers_se_regle(clone: Clone) -> None:
    """Un poste au profil mémoire différent doit pouvoir déplacer le plafond sans toucher au script.

    Une valeur absurde retombe sur le défaut plutôt que de faire rougir pytest sur ses arguments —
    un filet ne rend jamais un rouge qui ne parle pas du code.
    """
    clone.equipe_tout()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest", MAESTRO_PYTEST_WORKERS="2")
    assert workers_pytest(lancements_pytest(clone.appels())[0]) == 2

    clone.journal.unlink()
    acheve = clone.lance("--only", "pytest", MAESTRO_PYTEST_WORKERS="beaucoup")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    workers = workers_pytest(lancements_pytest(clone.appels())[0])
    assert workers is not None and 1 <= workers <= 8


def test_list_annonce_le_nombre_de_workers_reellement_passe(clone: Clone) -> None:
    """`--list` dit la commande jouée, pas celle du pipeline (#194) — plafond compris."""
    acheve = clone.lance("--complet", "--list", MAESTRO_PYTEST_WORKERS="3")
    assert acheve.returncode == 0, acheve.stderr
    assert "-n 3" in acheve.stdout
    assert "-n auto" not in acheve.stdout


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
    appels = appels_shellcheck(clone.appels())
    assert any("scripts/avec-crlf.sh" in appel for appel in appels)
    assert all(f"--severity={SEVERITE}" in appel for appel in appels)


def test_shellcheck_compte_les_remarques_de_la_severite_lue(clone: Clone) -> None:
    clone.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)
    acheve = clone.lance(
        "--only", "shellcheck",
        # La sortie dictée ne vaut que pour CE fichier : le job appelle shellcheck une fois par
        # fichier, une remarque concerne un fichier.
        MAESTRO_FAUX_SHELLCHECK_CIBLE="scripts/ci/local.sh",
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


# --- Un appel par fichier (#285) ------------------------------------------------------------------
# shellcheck est superlinéaire en taille totale reçue d'un coup : 21 scripts en un appel coûtent
# 38 s, les mêmes en vingt-et-un appels 12 s (le job passe de ~53 s à ~16 s). Deux invariants font
# tenir ce découpage — l'AGRÉGATION (un fichier rouge suffit à rougir le job, toutes les sorties
# vont dans le même journal) et le fait que le PIPELINE soit découpé de la même façon : shellcheck
# ne suit un `# shellcheck source=` que si le fichier sourcé est lui aussi sur la ligne de commande,
# donc découper d'un seul côté ferait diverger les deux verdicts.


def test_shellcheck_est_appele_une_fois_par_fichier(clone: Clone) -> None:
    """Un appel par script, et pas un appel portant les 21 — c'est tout le gain (#285)."""
    clone.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)
    (clone.racine / "scripts" / "second.sh").write_text(
        "#!/usr/bin/env bash\necho second\n", encoding="utf-8", newline="\n"
    )
    acheve = clone.lance("--only", "shellcheck")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    appels = appels_shellcheck(clone.appels())
    analyses = [fichiers_de_l_appel(appel) for appel in appels]
    assert all(len(f) == 1 for f in analyses), f"un appel par fichier attendu, reçu : {appels}"
    # Tous les scripts du dépôt jetable, chacun une fois — `local.sh` en fait partie (le clone en
    # porte une copie), d'où la comparaison avec ce que le disque contient plutôt qu'une liste.
    assert sorted(f[0] for f in analyses) == scripts_du_clone(clone)
    assert f"{len(analyses)} script(s)" in ligne_du_job(acheve.stdout, "shellcheck")


def test_shellcheck_agrege_les_codes_et_les_sorties_de_chaque_fichier(clone: Clone) -> None:
    """Un seul fichier rouge suffit à rougir le job, et sa raison se lit dans le journal commun.

    C'est ce qui rend le découpage licite : un appel par fichier ne doit pas rendre le code du
    DERNIER, ni perdre la sortie des précédents.
    """
    clone.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)
    for nom in ("propre-a.sh", "propre-b.sh"):
        (clone.racine / "scripts" / nom).write_text(
            "#!/usr/bin/env bash\necho ok\n", encoding="utf-8", newline="\n"
        )
    acheve = clone.lance(
        "--only", "shellcheck",
        MAESTRO_FAUX_SHELLCHECK_CIBLE="scripts/gitlab/lib.sh",
        MAESTRO_FAUX_SHELLCHECK_CODE="1",
        MAESTRO_FAUX_SHELLCHECK_SORTIE=(
            "In scripts/gitlab/lib.sh line 2:\\nSC2181 (warning): Check exit code.\\n"
        ),
    )

    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    ligne = ligne_du_job(acheve.stdout, "shellcheck")
    assert "ÉCHEC" in ligne and "1 remarque(s)" in ligne
    # Tous les fichiers ont été analysés — le job ne s'arrête pas au premier rouge…
    assert len(appels_shellcheck(clone.appels())) == len(scripts_du_clone(clone))
    # …et la sortie du fichier fautif est dans le journal commun, celui que le résumé cite.
    journal = (clone.racine / ".maestro" / "ci-local" / "shellcheck.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "SC2181" in journal and "scripts/gitlab/lib.sh" in journal


@pytest.mark.skipif(
    shutil.which("shellcheck") is not None,
    reason="un vrai shellcheck est sur le PATH : le repli docker n'est pas atteignable ici",
)
def test_le_repli_docker_ne_demarre_qu_un_seul_conteneur(clone: Clone) -> None:
    """Un `docker run` par fichier rendrait au démarrage ce que la boucle fait gagner.

    Le conteneur coûte ~2 s : vingt-et-un en coûteraient 42, soit plus que les 38 s de l'appel
    groupé qu'on remplace. La boucle vit donc DANS le conteneur.
    """
    clone.pose_shim("docker", corps=SHIM_DOCKER)
    acheve = clone.lance("--only", "shellcheck")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    runs = [appel for appel in clone.appels() if appel.startswith("docker run")]
    assert len(runs) == 1, f"un seul conteneur attendu, reçu : {runs}"
    assert IMAGE in runs[0]                     # l'image vient de .gitlab-ci.yml
    assert "scripts/gitlab/lib.sh" in runs[0]   # les fichiers sont les arguments de la boucle
    assert "for fichier in" in runs[0]          # …et la boucle est bien à l'intérieur


def test_le_pipeline_decoupe_shellcheck_comme_le_filet() -> None:
    """L'invariant qui protège du filet menteur — sur les fichiers VERSIONNÉS, pas le dépôt jetable.

    shellcheck ne suit un `# shellcheck source=` que si le fichier sourcé est lui aussi sur la
    ligne de commande. Un côté découpé et l'autre groupé, c'est donc un filet plus strict que la CI
    qu'il prédit : rouge en local, vert en pipeline, sur des remarques que rien n'explique.
    """
    pipeline = (RACINE / ".gitlab-ci.yml").read_text(encoding="utf-8")
    filet = (RACINE / "scripts" / "ci" / "local.sh").read_text(encoding="utf-8")
    # Le pipeline boucle sur ses fichiers au lieu de les passer tous d'un coup.
    assert re.search(r"for fichier in \$files", pipeline), \
        "le job shellcheck du pipeline doit boucler fichier par fichier (#285)"
    assert not re.search(r"shellcheck --severity=\w+ \$files", pipeline), \
        "l'appel groupé du pipeline ferait diverger son verdict de celui du filet"
    # Et aucun des deux ne passe `-x` : il rétablirait le lien entre fichiers, mais ferait
    # ré-analyser lib.sh par chacun des scripts qui la sourcent (34 s au lieu de 12).
    for source, nom in ((pipeline, ".gitlab-ci.yml"), (filet, "scripts/ci/local.sh")):
        assert not re.search(r"^\s*-?\s*shellcheck -x ", source, re.MULTILINE), \
            f"{nom} : `-x` annule le gain du découpage (#285)"


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


# --- Dérive des dépendances (#216) ----------------------------------------------------------------
# Le filet se sert de ce que `setup.sh` a posé ; encore faut-il que ce soit à jour. Il pose donc la
# question à `setup.sh --derive` (sans réseau ni écriture) et SIGNALE — installer dans le dos de qui
# lance un contrôle, ce serait changer l'environnement dont il attend un verdict (docs/10 §8.4).

# `setup.sh` factice : il journalise ce qu'on lui demande et rend la dérive qu'on lui a dictée.
SHIM_SETUP = """\
#!/usr/bin/env bash
printf 'setup %s\n' "$*" >> "$MAESTRO_FAUX_JOURNAL"
[ "$1" = --derive ] || exit 0
printf '%b' "${MAESTRO_FAUX_DERIVE:-}"
exit "${MAESTRO_FAUX_DERIVE_CODE:-0}"
"""

DERIVE_VENV = "venv\tpyproject.toml modifié depuis la dernière installation du venv\n"


def test_le_filet_signale_la_derive_sans_jamais_l_installer(clone: Clone) -> None:
    clone.equipe_tout()
    clone.pose_shim("setup.sh", clone.racine / "scripts", corps=SHIM_SETUP)

    acheve = clone.lance(
        "--only",
        "mypy",
        MAESTRO_FAUX_DERIVE=DERIVE_VENV,
        MAESTRO_FAUX_DERIVE_CODE="3",
    )

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Dépendances en retard" in acheve.stdout
    assert "pyproject.toml modifié" in acheve.stdout
    assert "bash scripts/setup.sh --only venv" in acheve.stdout
    assert clone.appels().count("setup --derive") == 1
    assert not [appel for appel in clone.appels() if "setup --only" in appel], (
        "le filet demande, il n'installe pas"
    )


def test_le_filet_se_tait_quand_les_dependances_sont_a_jour(clone: Clone) -> None:
    clone.equipe_tout()
    clone.pose_shim("setup.sh", clone.racine / "scripts", corps=SHIM_SETUP)

    acheve = clone.lance("--only", "mypy")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Dépendances en retard" not in acheve.stdout


def test_le_filet_tourne_sans_sonde_de_derive(clone: Clone) -> None:
    """Pas de `setup.sh` sous la main : le verdict reste rendu, sans bruit ni échec."""
    clone.equipe_tout()
    assert not (clone.racine / "scripts" / "setup.sh").exists()

    acheve = clone.lance("--only", "mypy")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "Verdict : VERT" in acheve.stdout
    assert "Dépendances en retard" not in acheve.stdout


# --- Un journal qui se lit là où on travaille (#234) ---------------------------------------------
# Un job rouge ne vaut que par la RAISON qu'il donne, et cette raison est dans son journal. Tant
# qu'il vivait sous `${TMPDIR:-/tmp}`, le filet renvoyait vers un chemin ABSOLU HORS du répertoire
# de travail — que le CLI refuse d'ouvrir sans approbation. En session interactive c'est un clic ;
# en session autonome (docs/10 §11) il n'y a personne pour le donner : le run de #200 a essayé cinq
# variantes puis a abandonné sans jamais savoir pourquoi ses tests échouaient (13 refus sur
# 5 sessions). C'est le seul refus qui prive d'une INFORMATION plutôt que d'un geste.

#: Assez de lignes pour dépasser l'extrait de 40 que le filet imprime — c'est ce qui déclenche le
#: « … (suite dans … ) », donc le renvoi vers le journal, donc le refus qu'on a corrigé.
SORTIE_LONGUE = "".join(f"maestro/module{i}.py:1: error: incompatible\\n" for i in range(60))


def journal_ci(clone: Clone) -> Path:
    return clone.racine / ".maestro" / "ci-local"


def test_le_journal_d_un_job_rouge_est_sous_la_racine_et_cite_en_relatif(clone: Clone) -> None:
    """Le cœur de #234 : le chemin imprimé se lit tel quel depuis le répertoire de travail."""
    clone.equipe_tout()
    acheve = clone.lance("--only", "mypy", MAESTRO_FAUX_MYPY_CODE="1",
                         MAESTRO_FAUX_MYPY_SORTIE=SORTIE_LONGUE)

    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert (journal_ci(clone) / "mypy.log").is_file(), "le journal doit vivre sous la racine"
    assert "journal : .maestro/ci-local/mypy.log" in acheve.stdout
    assert "(suite dans .maestro/ci-local/mypy.log)" in acheve.stdout
    # Le point qui compte vraiment : AUCUNE forme absolue ne doit sortir, ni celle de la racine du
    # clone, ni celle de l'ancien emplacement. C'est l'absolu qui déclenchait le refus, pas /tmp.
    assert str(clone.racine) not in acheve.stdout
    assert "maestro-ci-local" not in acheve.stdout
    assert not (clone.tmp / "maestro-ci-local").exists(), \
        "plus rien du filet ne s'écrit dans le temporaire du système"


def test_le_journal_fait_table_rase_a_chaque_lancement(clone: Clone) -> None:
    """Dans `/tmp` le système faisait le ménage ; sous la racine, personne ne le ferait.

    Et un `pytest.log` de la veille laissé à côté d'un run qui n'a pas joué pytest est **pire
    qu'absent** : il ment sur ce qui vient d'être vérifié.
    """
    clone.equipe_tout()
    clone.lance("--only", "mypy")
    fossile = journal_ci(clone) / "pytest.log"
    fossile.write_text("verdict d'hier\n", encoding="utf-8", newline="\n")

    acheve = clone.lance("--only", "mypy")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not fossile.exists(), "un journal d'un job non joué survivrait au lancement suivant"
    assert (journal_ci(clone) / "mypy.log").is_file()


def test_chaque_recours_au_temporaire_du_systeme_reste_justifie() -> None:
    """L'audit de docs/10 §8.5 se refait par RECHERCHE, jamais par réenquête.

    Ce qu'un script invite à lire va sous `.maestro/<domaine>/` ; ce que personne ne lit — miroir
    de shellcheck, brouillon de `queue.sh`, fichiers porteurs de secrets d'`env-pull.sh` — reste
    dans `${TMPDIR:-/tmp}` **avec la raison en commentaire**. Un nouvel emploi non justifié tombe
    ici plutôt que dans le `permission_denials` d'un run, six mois plus tard.
    """
    motif = "${TMPDIR:-/tmp}"
    sans_justification: list[str] = []
    for script in sorted((RACINE / "scripts").rglob("*.sh")):
        source = script.read_text(encoding="utf-8")
        lignes = source.splitlines()
        for numero, ligne in enumerate(lignes):
            if motif not in ligne or ligne.lstrip().startswith("#"):
                continue
            # Deux exigences, parce qu'elles disent deux choses : le FICHIER a été arbitré contre
            # le partage de §8.5 (il cite le ticket), et cet emploi-ci porte une explication assez
            # près pour se lire avec lui. Un `${TMPDIR}` déposé sans un mot ne passe ni l'une ni
            # l'autre.
            voisinage = lignes[max(0, numero - 12):numero]
            explique = any(ligne_haut.lstrip().startswith("#") for ligne_haut in voisinage)
            if "#234" not in source or not explique:
                sans_justification.append(f"{script.relative_to(RACINE).as_posix()}:{numero + 1}")
    assert not sans_justification, (
        "recours au temporaire du système sans la raison en commentaire (docs/10 §8.5) : "
        + ", ".join(sans_justification)
    )


# --- Le typage web, jouable par `npm run` (#236) -------------------------------------------------
# `npm run:*` est autorisé à une session autonome, jamais un `./node_modules/.bin/tsc` : 7 refus sur
# 2 sessions pour la même vérification, retentée sous quatre habillages. Exposer le script tarit le
# besoin au lieu d'élargir les droits — encore faut-il que quelqu'un le joue, sans quoi il pourrit.


def test_web_build_verifie_le_typage_avant_les_tests_et_le_build(clone: Clone) -> None:
    """L'ordre va du plus rapide au plus lent : l'erreur de typage tombe en clair, et tôt."""
    clone.equipe_tout()
    acheve = clone.lance("--only", "web-build")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    lances = [a for a in clone.appels() if a.startswith("npm ")]
    jalons = ("run lint", "run typecheck", "test", "run build")
    etapes = [a for a in lances if any(m in a for m in jalons)]
    rangs = {mot: next(i for i, a in enumerate(etapes) if mot in a)
             for mot in ("run lint", "run typecheck", "run build")}
    assert rangs["run lint"] < rangs["run typecheck"] < rangs["run build"]
    assert "tsc" in ligne_du_job(acheve.stdout, "web-build")


def test_un_typage_rouge_arrete_le_job_avant_vitest(clone: Clone) -> None:
    """Le gain du `typecheck` séparé : l'erreur est nommée, pas un `next build` rouge tardif."""
    clone.equipe_tout()
    clone.pose_shim(
        "npm",
        clone.racine / ".tools" / "node" / f"v{NODE_PIN}",
        corps=(
            "#!/usr/bin/env bash\n"
            "printf 'npm %s\\n' \"$*\" >> \"$MAESTRO_FAUX_JOURNAL\"\n"
            "case \" $* \" in *' run typecheck '*) exit 1 ;; esac\n"
            "exit 0\n"
        ),
    )
    acheve = clone.lance("--only", "web-build")

    assert acheve.returncode == 1
    ligne = ligne_du_job(acheve.stdout, "web-build")
    assert "tsc --noEmit" in ligne and "typage" in ligne
    assert not [a for a in clone.appels() if a.startswith("npm ") and "run build" in a], \
        "le job doit s'arrêter au typage, sans payer le build"


def test_le_script_typecheck_existe_et_la_ci_le_joue() -> None:
    """Les deux moitiés du remède : le script exposé, et quelqu'un pour le jouer.

    Le fichier VERSIONNÉ, pas la copie du dépôt jetable — c'est le régime réel des sessions qui
    est en jeu, et un `typecheck` que personne ne lance ne resterait pas vert longtemps.
    """
    paquet = json.loads((RACINE / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    assert paquet["scripts"].get("typecheck") == "tsc --noEmit", \
        "sans ce script, une session n'a que `node …/tsc` — que rien n'autorise (#236)"
    pipeline = (RACINE / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "npm run typecheck" in pipeline, "un script jamais joué en CI finit par ne plus passer"
