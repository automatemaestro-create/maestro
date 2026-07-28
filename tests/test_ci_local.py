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
  inventer des SC1017 que la CI ne verra jamais.

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

    def pose_outil_venv(self, nom: str) -> None:
        """Un outil du venv du dépôt, dans les deux dispositions (Windows et Unix)."""
        self.pose_shim(f"{nom}.exe", self.racine / ".venv" / "Scripts")
        self.pose_shim(nom, self.racine / ".venv" / "bin")

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
    """Seuil et sévérité sont LUS dans `.gitlab-ci.yml` : le filet suit le pipeline."""
    acheve = clone.lance("--list")
    assert acheve.returncode == 0, acheve.stderr
    assert f"--severity={SEVERITE}" in acheve.stdout
    assert f"--cov-fail-under={SEUIL}" in acheve.stdout
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
    acheve = clone.lance(
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
        "--only", "pytest",
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
    assert not any(a.startswith("pytest ") for a in clone.appels())


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
    assert "eslint + next build verts" in ligne_du_job(acheve.stdout, "web-build")
    lances = [a for a in clone.appels() if a.startswith("npm ")]
    assert any("run lint" in a for a in lances)
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
