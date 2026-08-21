"""Tests du filet CI local — `scripts/ci/local.sh` (ticket #156, lot final du parent #155).

Le script rejoue en local les jobs de `.github/workflows/ci.yml` avant le push : découvrir un
échec de lint par le pipeline, c'est l'apprendre après plusieurs minutes facturées, pour une faute
de frappe (docs/10 §8). Un filet qui MENT — vert alors que la CI serait rouge, ou l'inverse — est
pire que pas de filet : ces tests épinglent donc ses verdicts.

Ce qui est vérifié :

* les réglages sont **lus dans `.github/workflows/ci.yml`** (sévérité shellcheck, seuil de
  couverture) plutôt que recopiés — le filet suit le pipeline quand celui-ci change ;
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
# L'image du conteneur shellcheck n'est PLUS lue dans la CI (#344) : le job `shellcheck` de
# GitHub Actions se sert du binaire préinstallé sur le runner. Elle n'est qu'un repli local, et
# c'est MAESTRO_SHELLCHECK_IMAGE qui la fixe ici — poser la valeur d'essai par l'environnement
# épingle précisément ce changement : si le filet se remettait à la lire dans le pipeline, il n'y
# trouverait plus rien.
IMAGE = "koalaman/shellcheck-alpine:v0.0.0-essai"

# Pipeline synthétique. La ligne de commentaire porte volontairement une AUTRE sévérité : le motif
# de lecture inclut le nom de la commande, c'est ce qui l'empêche d'attraper la mauvaise valeur.
WORKFLOW_CI = f"""\
name: CI
on:
  pull_request:
  workflow_dispatch:

jobs:
  shellcheck:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: |
          # à durcir en --severity=error quand le dépôt sera propre
          for fichier in $files; do
            shellcheck --severity={SEVERITE} "$fichier" || code=1
          done

  pytest:
    runs-on: ubuntu-latest
    steps:
      - run: pytest --cov=maestro --cov-fail-under={SEUIL}

  web-build:
    runs-on: ubuntu-latest
    steps:
      - run: npm run typecheck
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
    # Le PIÈGE de #375, posé dans le dépôt jetable comme il l'est dans le vrai : le mot
    # « migration » traîne dans la prose d'une suite qui n'a rien à voir avec
    # `scripts/migration/`. Un repli qui cherche le nom NU du dossier la sélectionne ; un repli
    # qui cherche le chemin ne la voit pas. `MOT_PIEGE` en fait un invariant vérifié, pas un
    # décor : les tests s'assurent que le piège existe avant de conclure qu'il n'a pas fonctionné.
    "tests/test_horloge.py": (
        "# une suite applicative qui ne cite aucun script — même en parlant de migration\n"
    ),
    "tests/test_outillage.py": '"""Pilote scripts/gitlab/lib.sh dans un dépôt jetable."""\n',
    "scripts/gitlab/lib.sh": "#!/usr/bin/env bash\necho lib\n",
    # Le piège de #372, reproduit à l'identique. Cette suite-ci pilote `worktree.sh` — c'est donc
    # une suite d'outillage, et elle DOIT être jouée quand `worktree.sh` bouge. Mais elle contient
    # aussi `hashlib.sha256`, dont `lib.sh` est un SUFFIXE : un matcher par sous-chaîne la tirait
    # dans le périmètre de tout diff touchant `scripts/gitlab/lib.sh`, c'est-à-dire le diff le plus
    # courant du dépôt. Dans le vrai dépôt, c'était `tests/test_setup.py` — 2 min 08 s payées pour
    # un mot.
    #
    # L'appât ne peut PAS être `scripts/setup.sh`, si tentant que soit le rappel du vrai cas :
    # `test_le_filet_tourne_sans_sonde_de_derive` vérifie plus bas que ce fichier-là est ABSENT du
    # dépôt jetable — c'est ainsi qu'il éteint la sonde de dérive de #216. Un appât qui le pose
    # rend ce test-là rouge, pour une raison sans aucun rapport avec ce qu'il garde.
    "tests/test_empreinte.py": (
        '"""Pilote scripts/git/worktree.sh dans un dépôt jetable."""\n'
        "import hashlib\n\n\n"
        "def empreinte(chemin) -> str:\n"
        "    return hashlib.sha256(chemin.read_bytes()).hexdigest()\n"
    ),
    "scripts/git/worktree.sh": "#!/usr/bin/env bash\necho worktree\n",
    # Le pendant du piège précédent, côté REPLI PAR DOSSIER. Cette suite ne porte `gitlab` qu'au
    # milieu d'un mot — comme `tests/test_secrets.py` dans le vrai dépôt. Elle NE doit PAS être
    # sélectionnée quand un fichier de `scripts/gitlab/` que personne ne nomme change : depuis
    # #375 le repli cherche le chemin `scripts/gitlab/`, pas le nom nu `gitlab`. L'appât a été
    # posé par #372 pour l'invariant inverse, du temps du nom nu — voir le test qui s'en sert.
    "tests/test_secrets.py": (
        '"""Les secrets ne fuient pas."""\n\n\n'
        "def test_un_etage_lint_rouge_arrete_le_pipeline_comme_gitlab() -> None:\n"
        "    pass\n"
    ),
    # Les deux scripts que PERSONNE ne nomme — l'un dans un dossier imbriqué dont le nom nu est un
    # mot courant, l'autre à la racine de `scripts/`, dont le chemin est une sous-chaîne de tout
    # `scripts/gitlab/lib.sh` cité quelque part. Les cinq scripts arrivés avec la migration GitHub
    # sont dans ce cas, et c'est ce qui a rendu le défaut visible (#375).
    "scripts/migration/inventaire.sh": "#!/usr/bin/env bash\necho inventaire\n",
    "scripts/orphelin.sh": "#!/usr/bin/env bash\necho orphelin\n",
    # Une suite qui relit tout un répertoire le désigne par son CHEMIN, jamais par le nom de ses
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

#: Le `pyproject.toml` du dépôt jetable. Son CONTENU n'a aucune importance — seule compte sa
#: présence, l'étiquette de l'image du régime conteneur étant l'empreinte de ce fichier et du
#: Dockerfile (#372). Il est volontairement minuscule : ce n'est pas un pyproject qu'on teste.
PYPROJECT = '[project]\nname = "jetable"\nversion = "0.0.0"\n'

#: Le nom nu du dossier de `scripts/migration/inventaire.sh`, et le mot semé dans la prose de
#: `tests/test_horloge.py`. Les deux emplois se lisent d'ici : le jour où l'un des deux change,
#: le piège de #375 se désamorce en silence et les tests qui s'en servent ne prouvent plus rien.
MOT_PIEGE = "migration"

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
#
# Depuis #372 il sert AUSSI le régime conteneur du job pytest, qui lui pose trois questions de plus
# — `version` (le démon répond-il ?), `image inspect` (l'image est-elle là ?) et `build`. Chacune a
# son propre code de retour, et TOUS valent 0 par défaut : les tests du repli shellcheck écrits
# avant ce ticket continuent de voir le docker complaisant qu'ils attendent.
SHIM_DOCKER = """\
#!/usr/bin/env bash
printf 'docker %s\\n' "${*//$'\\n'/ }" >> "$MAESTRO_FAUX_JOURNAL"
case "$1" in
  version) exit "${MAESTRO_FAUX_DOCKER_VERSION_CODE:-0}" ;;
  build) exit "${MAESTRO_FAUX_DOCKER_BUILD_CODE:-0}" ;;
  image) exit "${MAESTRO_FAUX_DOCKER_INSPECT_CODE:-0}" ;;
  run) ;;
  *) exit 0 ;;
esac
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

    def pose_npm(self, corps: str | None = None) -> None:
        """Le npm vendoré, dans les DEUX dispositions — comme `pose_outil_venv` pour le venv.

        `node_bindir` (scripts/ci/local.sh) résout `.tools/node/v<pin>` sous Windows et
        `.tools/node/v<pin>/bin` ailleurs. N'écraser qu'une des deux laisse l'autre en place, donc
        laisse le script trouver le shim d'origine : un test qui croit avoir posé un npm ROUGE
        obtient un vert, et son assertion tombe sur la seule plateforme qui n'est pas celle du
        poste (#333).
        """
        base = self.racine / ".tools" / "node" / f"v{NODE_PIN}"
        self.pose_shim("npm", base, corps)
        self.pose_shim("npm", base / "bin", corps)

    def pose_node(self) -> None:
        """Le Node vendoré et son npm, dans les deux dispositions."""
        base = self.racine / ".tools" / "node" / f"v{NODE_PIN}"
        self.pose_shim("node.exe", base)
        self.pose_shim("node", base / "bin")
        self.pose_npm()
        (self.racine / "apps" / "web" / "node_modules").mkdir(parents=True, exist_ok=True)

    def equipe_tout(self) -> None:
        self.pose_shim("shellcheck", corps=SHIM_SHELLCHECK)
        for outil in ("ruff", "pytest", "mypy"):
            self.pose_outil_venv(outil)
        self.pose_outil_venv("python", corps=SHIM_PYTHON)
        self.pose_node()

    def equipe_conteneur(self) -> None:
        """Le régime conteneur de #372 : un docker qui joue le jeu, et de quoi étiqueter l'image.

        Les deux fichiers sont COMMITÉS PUIS POUSSÉS, pas seulement écrits : `pyproject.toml` est
        un fichier transverse au sens du périmètre (#214), donc le laisser en travail non commité
        ramènerait chaque test d'ici à la suite entière — les assertions sur les suites choisies ne
        diraient plus rien de la règle qu'elles croient épingler.
        """
        self.pose_shim("docker", corps=SHIM_DOCKER)
        (self.racine / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8", newline="\n")
        shutil.copy2(
            RACINE / "scripts" / "ci" / "pytest.Dockerfile",
            self.racine / "scripts" / "ci" / "pytest.Dockerfile",
        )
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "chore: régime conteneur")
        self.git("push", "--quiet", "origin", "main")

    # --- exécution ---
    def _lance(
        self, script: str, args: tuple[str, ...], reglages: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        environnement.update(
            {
                "PATH": os.pathsep.join([str(self.fauxbin), environnement.get("PATH", "")]),
                "TMPDIR": str(self.tmp),
                "MAESTRO_FAUX_JOURNAL": str(self.journal),
                # L'image de repli n'est plus lue dans la CI (#344) : on la pose ici.
                "MAESTRO_SHELLCHECK_IMAGE": IMAGE,
            }
        )
        environnement.update(reglages)
        assert BASH is not None
        return subprocess.run(  # noqa: S603
            [BASH, str(self.racine / "scripts" / "ci" / script), *args],
            cwd=str(self.racine),
            env=environnement,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

    def lance(self, *args: str, **reglages: str) -> subprocess.CompletedProcess[str]:
        """Le filet — il rend un verdict sur le périmètre du diff."""
        return self._lance("local.sh", args, reglages)

    def lance_pytest(self, *args: str, **reglages: str) -> subprocess.CompletedProcess[str]:
        """Le lanceur d'itération (#405) — il joue ce qu'on lui passe, et rien d'autre.

        Même environnement que le filet, à dessein : les deux sourcent `pytest-regime.sh`, donc
        c'est le MÊME `docker` factice et le même venv factice qui répondent. Un harnais qui les
        équiperait différemment ne pourrait plus rien dire du partage qu'on vient épingler ici.
        """
        return self._lance("pytest.sh", args, reglages)

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
    # Les TROIS fichiers du filet, pas seulement son point d'entrée : depuis #405 la plomberie du
    # régime conteneur vit dans `pytest-regime.sh`, que `local.sh` ET `pytest.sh` sourcent. N'en
    # copier qu'un rendrait le clone jetable incapable de démarrer — et surtout, ne pas copier
    # `pytest.sh` reviendrait à tester un partage dont un seul des deux bénéficiaires est présent.
    for fichier in ("local.sh", "pytest-regime.sh", "pytest.sh"):
        shutil.copy2(RACINE / "scripts" / "ci" / fichier, racine / "scripts" / "ci" / fichier)
    (racine / ".github" / "workflows").mkdir(parents=True)
    (racine / ".github" / "workflows" / "ci.yml").write_text(
        WORKFLOW_CI, encoding="utf-8", newline="\n"
    )
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
    #
    # C'est devenu le garde-fou CENTRAL de ce fichier avec #372 : le job pytest joue désormais dans
    # un conteneur Linux dès que le démon répond — ce qui est le cas sur le poste comme sur les
    # runners GitHub. Sans ce shim, chaque test d'ici monterait un vrai conteneur sur son dépôt
    # jetable : lent, et surtout FAUX — les shims du PATH ne franchissent pas la frontière du
    # conteneur, donc plus aucune des décisions observées ici ne le serait. Un `docker` qui refuse
    # tout renvoie le filet au régime natif, celui que la quasi-totalité de ces tests épinglent.
    # Les tests du régime conteneur, eux, remplacent ce shim par SHIM_DOCKER (bloc « Où pytest
    # joue »). Ne pas doubler ce mécanisme par un MAESTRO_PYTEST_REGIME posé dans `lance()` : deux
    # verrous pour une porte, c'est un verrou qu'on oubliera de tourner.
    clone.pose_shim("docker", corps="#!/usr/bin/env bash\nexit 1\n")
    return clone


# --- Les réglages viennent du pipeline ------------------------------------------------------------


def test_list_reprend_les_reglages_du_workflow(clone: Clone) -> None:
    """Seuil et sévérité sont LUS dans `.github/workflows/ci.yml` : le filet suit le pipeline.

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
# pipeline de la PR (docs/10 §8). Un périmètre qui se trompe est pire que pas de périmètre : ces
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


def test_un_nom_de_fichier_ne_matche_pas_au_milieu_d_un_mot(clone: Clone) -> None:
    """`lib.sh` ne doit pas matcher le `hashlib.sha256` d'une suite qui parle d'autre chose (#372).

    Ce n'était pas une curiosité : dans le vrai dépôt, `tests/test_setup.py` rejoignait ainsi le
    périmètre de TOUT diff touchant `scripts/gitlab/lib.sh` — le plus courant du dépôt — pour
    2 min 08 s à chaque lancement du filet, sur un mot qui ne nomme aucun script.
    """
    clone.equipe_tout()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert suites_jouees(clone.appels()) == ["tests/test_outillage.py"]


def test_l_ancrage_ne_fait_perdre_aucune_suite_qui_nomme_vraiment_le_script(clone: Clone) -> None:
    """L'autre moitié du test précédent, et la seule qui puisse rendre un faux vert.

    Ancrer le nom, c'est risquer de ne plus voir une suite qui cite bel et bien le script. La même
    suite-appât sert donc de témoin : elle pilote `worktree.sh`, et un diff sur `worktree.sh` doit
    la jouer.
    """
    clone.equipe_tout()
    clone.modifie("scripts/git/worktree.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert suites_jouees(clone.appels()) == ["tests/test_empreinte.py"]
    assert "worktree.sh" in ligne_du_job(acheve.stdout, "pytest")


def test_le_repli_par_dossier_ignore_le_nom_au_milieu_d_un_mot(clone: Clone) -> None:
    """Le repli par dossier est exact MÊME QUAND il trouve quelque chose (#372 réconcilié à #375).

    Ce test-ci a changé de sens en cours de route, et c'est le fait notable. #372 le tenait pour
    le témoin de la souplesse du repli : celui-ci cherchait alors le nom NU du dossier, donc
    `gitlab`, donc `tests/test_secrets.py` qui ne le porte qu'au milieu de `…_comme_gitlab`, et
    l'ancrer aurait perdu cette suite. #375 a depuis remplacé le nom nu par le CHEMIN avec son
    séparateur (`scripts/gitlab/`), ce qui rend cette sélection-là non seulement perdue mais
    INDÉSIRABLE : la suite ne teste rien de `scripts/gitlab/`, elle parle d'un pipeline.

    Les tests de #375 couvrent le cas où le repli ne trouve RIEN et élargit donc à toute la suite
    (`scripts/migration/inventaire.sh`, `scripts/orphelin.sh`). Celui-ci couvre l'autre moitié,
    la seule qui puisse encore sur-sélectionner en silence : le repli trouve une suite légitime —
    `tests/test_outillage.py` cite `scripts/gitlab/lib.sh` — et ne doit pas ramasser la voisine
    au passage.

    L'appât reste donc dans l'arborescence, sur l'invariant inverse de celui pour lequel il y a
    été posé.
    """
    clone.equipe_tout()
    clone.modifie("scripts/gitlab/inconnu.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    # L'appât existe bien, et porte `gitlab` sans le chemin : sans ce contrôle, un jour où la
    # suite-appât aurait perdu son mot, l'assertion ci-dessous passerait sans rien prouver.
    appat = (clone.racine / "tests" / "test_secrets.py").read_text(encoding="utf-8")
    assert "gitlab" in appat and "scripts/gitlab/" not in appat
    assert suites_jouees(clone.appels()) == ["tests/test_outillage.py"]
    assert "gitlab/" in ligne_du_job(acheve.stdout, "pytest")


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


def test_un_fichier_anonyme_est_rattrape_par_le_chemin_de_son_dossier(clone: Clone) -> None:
    """Repli du nom de fichier vers le CHEMIN du dossier : sans lui, toucher un prompt de
    `.claude/commands/` rejouerait les 1100 tests — aucune suite ne cite un prompt par son nom,
    elles parcourent le répertoire."""
    clone.equipe_tout()
    clone.modifie(".claude/commands/ticket-start.md", "une ligne de plus\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert suites_jouees(clone.appels()) == ["tests/test_prompts.py"]
    # Le motif annonce le CHEMIN cherché et non le nom nu du dossier (#375) : c'est ce qui permet
    # de relire un verdict sans rejouer la recherche pour savoir ce qui a été comparé à quoi.
    assert ".claude/commands/" in ligne_du_job(acheve.stdout, "pytest")


def test_le_repli_par_dossier_ne_matche_pas_une_sous_chaine(clone: Clone) -> None:
    """#375 — le défaut : le repli cherchait le nom NU du dossier. Sur `scripts/migration/`, le
    mot « migration » traîne dans la prose d'une suite applicative sans rapport, et le filet
    partait sur elle en annonçant « périmètre : 1 suite (migration/) » — un faux vert MOTIVÉ, pire
    qu'un périmètre absent, sur un script que rien ne teste.

    Le repli remplaçant l'élargissement, ce n'était pas du temps perdu mais de la couverture
    perdue : la règle d'or veut que tout fichier dont personne ne parle ramène la suite entière.
    """
    clone.equipe_tout()
    # Le piège d'abord — sans lui le test passerait sur une question jamais posée.
    piege = (clone.racine / "tests" / "test_horloge.py").read_text(encoding="utf-8")
    assert MOT_PIEGE in piege, "le mot piégé a disparu de la suite applicative : test désamorcé"
    assert MOT_PIEGE not in (clone.racine / "tests" / "test_outillage.py").read_text(
        encoding="utf-8"
    ), "le piège doit être posé sur une suite SANS rapport avec scripts/migration/"

    clone.modifie(f"scripts/{MOT_PIEGE}/inventaire.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert suites_jouees(clone.appels()) == []
    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "aucune suite ne nomme inventaire.sh" in ligne
    assert "tests/test_horloge.py" not in ligne


def test_le_repli_par_dossier_ignore_un_dossier_de_premier_niveau(clone: Clone) -> None:
    """Le même défaut, un cran plus haut : `scripts/` EST un chemin, mais c'est une sous-chaîne de
    tout `scripts/gitlab/lib.sh` cité quelque part — il ne désigne aucun répertoire en
    particulier. Un fichier posé à la racine de `scripts/` que personne ne nomme élargit donc,
    au lieu d'hériter des suites qui parlent d'un script voisin."""
    clone.equipe_tout()
    assert "scripts/" in (clone.racine / "tests" / "test_outillage.py").read_text(
        encoding="utf-8"
    ), "le piège suppose une suite qui cite un chemin sous scripts/ : test désamorcé"

    clone.modifie("scripts/orphelin.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert suites_jouees(clone.appels()) == []
    assert "aucune suite ne nomme orphelin.sh" in ligne_du_job(acheve.stdout, "pytest")


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
    """`--list` dit la commande jouée, pas celle du pipeline (#194) — plafond compris.

    Le nombre annoncé est confronté à celui que le script PASSE vraiment, jamais à une constante :
    c'est le contrat que ce bloc épingle (« jamais plus que le plafond, jamais plus que les
    cœurs »), et un `-n 3` en dur le contredisait — vert sur un poste à 16 cœurs, rouge sur un
    runner à 2, où `min(cœurs, 8)` ramène 3 à 2 (#333). Le rejouer ici reviendrait en plus à
    recopier la formule hors du script, seul endroit où elle doit vivre.
    """
    clone.equipe_tout()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")

    annonce = clone.lance("--complet", "--list", MAESTRO_PYTEST_WORKERS="3")
    assert annonce.returncode == 0, annonce.stderr
    assert "-n auto" not in annonce.stdout, "`--list` doit annoncer le plafond, pas le pipeline"

    joue = clone.lance("--complet", "--only", "pytest", MAESTRO_PYTEST_WORKERS="3")
    assert joue.returncode == 0, joue.stdout + joue.stderr
    workers = workers_pytest(lancements_pytest(clone.appels())[0])

    assert workers is not None
    assert f"-n {workers}" in annonce.stdout, (
        f"`--list` annonce autre chose que les {workers} workers réellement passés :\n"
        f"{annonce.stdout}"
    )


# --- OÙ pytest joue (#372) ------------------------------------------------------------------------
# Le job pytest joue dans un CONTENEUR LINUX, et le régime natif n'est plus qu'un repli. La raison
# n'est pas la propreté : les suites d'outillage sont faites à 100 % de sous-processus shell, donc
# leur durée est une fonction du prix d'un fork — ~800 ms sous Windows contre < 1 ms sous Linux.
# Mesuré dos à dos le 2026-08-21 : tests/test_worktree.py 424 s → 13,5 s (×31), les six suites du
# périmètre `scripts/**` 14 min 33 → 52,9 s, la suite ENTIÈRE 1 min 51.
#
# Le second gain n'est pas de vitesse et c'est le plus important : le filet joue sur l'OS DU
# VERDICT. #332/#333 ont montré que 285 tests d'outillage n'avaient jamais tourné ailleurs que sous
# Windows, et que le premier runner Linux muni de git en avait trouvé 16 rouges d'un coup.
#
# Ces tests n'ouvrent AUCUN conteneur : c'est SHIM_DOCKER qui répond, et ce sont les DÉCISIONS du
# script qu'on lit dans son journal — quel régime, quelle image, quel montage, et surtout ce que le
# verdict en dit.


def lancements_conteneur(appels: list[str]) -> list[str]:
    """Les `docker run` qui ont joué la suite — jamais un `docker build` ni un `image inspect`."""
    return [appel for appel in appels if appel.startswith("docker run")]


def cible_du_montage(lance: str) -> str:
    """Le chemin DANS LE CONTENEUR où le dépôt est monté (`-v <hôte>:<cible>`)."""
    trouve = re.search(r" -v \S+?:(/\S*) ", lance)
    assert trouve, f"aucun montage lisible dans : {lance}"
    return trouve.group(1)


def etiquette_image(appels: list[str]) -> str:
    """L'étiquette passée à `docker build --tag` (l'empreinte de ce dont l'image est faite)."""
    for appel in appels:
        trouve = re.search(r"--tag (\S+)", appel)
        if trouve:
            return trouve.group(1)
    raise AssertionError(f"aucun `docker build --tag` dans : {appels}")


def test_pytest_joue_dans_le_conteneur_quand_docker_repond(clone: Clone) -> None:
    """Le régime nominal : un `docker run`, et PAS de `python -m pytest` sur le poste."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    runs = lancements_conteneur(clone.appels())
    assert len(runs) == 1, f"un seul conteneur attendu, reçu : {runs}"
    assert not lancements_pytest(clone.appels()), \
        "le venv du poste ne doit pas jouer la suite quand le conteneur le fait"
    # Le dépôt est MONTÉ, jamais copié : c'est le code de la branche — travail non commité compris —
    # qui est testé, exactement ce sur quoi le pipeline se prononcera.
    montage = cible_du_montage(runs[0])
    assert f" -w {montage} " in runs[0], "le conteneur doit travailler dans le dépôt monté"
    assert f"PYTHONPATH={montage}" in runs[0], \
        "sans PYTHONPATH, `import maestro` n'a plus de source : l'image efface celle du stub"
    # …et la suite retenue par le périmètre est bien celle qui est jouée.
    assert "tests/test_outillage.py" in runs[0]


def test_le_depot_n_est_jamais_monte_a_la_racine_du_conteneur(clone: Clone) -> None:
    """Trouvé par les tests, et à ne pas défaire.

    Monté à `/w`, le PARENT du dépôt est `/` : `test_projets.py::test_depot_maestro_refuse` reçoit
    alors `racine-de-disque` là où il attend `au-dessus-du-depot-maestro`. Un rouge qui ne dit rien
    du code et tout du point de montage — le genre d'écart qui fait douter du filet lui-même. Il
    faut donc au moins deux niveaux, pour qu'il existe un « au-dessus » qui ne soit pas la racine.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    clone.lance("--only", "pytest")

    montage = cible_du_montage(lancements_conteneur(clone.appels())[0])
    segments = [morceau for morceau in montage.split("/") if morceau]
    assert len(segments) >= 2, (
        f"le dépôt est monté à {montage!r} : son parent serait la racine du disque, "
        "et test_depot_maestro_refuse rougirait pour une raison qui n'est pas la sienne"
    )


def test_le_conteneur_joue_n_auto_comme_la_ci(clone: Clone) -> None:
    """Le plafond de #285 est un fait sur la MÉMOIRE DU POSTE Windows, pas un fait sur pytest.

    Re-mesuré dans le conteneur le 2026-08-21 sur les six suites du périmètre (598 tests) :
    `-n 4` 177 s · `-n 8` 63 s · `-n 16` 56 s · `-n auto` 46 s, tous VERTS. Sous Windows, `-n 16`
    faisait pire que `-n 8` (11 min 37) ET rougissait quatre tests de la vue console par saturation.
    Il n'y a donc rien à plafonner là-bas — et `-n auto` est en prime le drapeau du pipeline, soit
    un écart de moins entre le filet et le verdict qu'il prédit.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    clone.lance("--only", "pytest")

    lance = lancements_conteneur(clone.appels())[0]
    assert " -n auto " in f"{lance} ", f"le conteneur doit jouer `-n auto` : {lance}"

    # L'autre moitié de l'invariant, sur le VRAI pipeline : si la CI cessait de jouer `-n auto`,
    # ce test-ci resterait vert en épinglant un accord qui n'existerait plus.
    pipeline = (RACINE / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"pytest -n auto", pipeline), \
        "le filet s'aligne sur `-n auto` : le pipeline doit continuer de le jouer"


def test_sans_docker_le_filet_retombe_en_natif_et_le_dit(clone: Clone) -> None:
    """Le repli existe — mais il est ANNONCÉ, et c'est là tout le sujet.

    Un filet qui retomberait en silence rendrait un vert de quinze minutes en se faisant passer
    pour un vert d'une minute, et surtout un vert qui n'a pas vu ce que la CI verra. Le régime est
    donc dit deux fois : sur la ligne du job, et dans un bloc d'avertissement avant le verdict.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--only", "pytest", MAESTRO_FAUX_DOCKER_VERSION_CODE="1")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not lancements_conteneur(clone.appels()), "aucun conteneur sans démon Docker"
    assert lancements_pytest(clone.appels()), "le repli doit bel et bien jouer la suite"
    assert "NATIF" in ligne_du_job(acheve.stdout, "pytest")
    assert "Docker" in acheve.stdout, "le repli doit nommer sa cause avant le verdict"


def test_le_regime_conteneur_exige_echoue_au_lieu_de_retomber(clone: Clone) -> None:
    """`--conteneur` est une exigence, pas une préférence : sans Docker, le job est IGNORÉ.

    C'est ce qui le rend utilisable là où un repli silencieux ferait passer « Docker manquait »
    pour « tout va bien » — un pipeline, un test, un run autonome que personne ne regarde.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")
    acheve = clone.lance("--conteneur", "--only", "pytest", MAESTRO_FAUX_DOCKER_VERSION_CODE="1")

    ligne = ligne_du_job(acheve.stdout, "pytest")
    assert "IGNORÉ" in ligne, f"attendu IGNORÉ, reçu : {ligne}"
    assert not lancements_pytest(clone.appels()), \
        "un régime exigé ne doit pas retomber en silence sur celui qu'on n'a pas demandé"
    assert acheve.returncode == 0                       # ignoré ≠ rouge, comme tout outil absent
    assert clone.lance(
        "--conteneur", "--strict", "--only", "pytest", MAESTRO_FAUX_DOCKER_VERSION_CODE="1"
    ).returncode == 1                                    # …mais bloquant sous --strict


def test_l_image_est_construite_quand_elle_manque_et_pas_sinon(clone: Clone) -> None:
    """La seule chose que ce filet fabrique — et il ne la refabrique pas à chaque lancement."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")

    absente = clone.lance("--only", "pytest", MAESTRO_FAUX_DOCKER_INSPECT_CODE="1")
    assert absente.returncode == 0, absente.stdout + absente.stderr
    assert [appel for appel in clone.appels() if appel.startswith("docker build")], \
        "une image absente doit être construite"
    assert "construction" in absente.stdout, \
        "une construction de plusieurs minutes se dit : muette, elle passe pour un blocage"

    clone.journal.unlink()
    presente = clone.lance("--only", "pytest")
    assert presente.returncode == 0, presente.stdout + presente.stderr
    assert not [appel for appel in clone.appels() if appel.startswith("docker build")], \
        "une image déjà là ne se reconstruit pas"


def test_l_etiquette_de_l_image_suit_pyproject_et_le_dockerfile(clone: Clone) -> None:
    """L'étiquette EST l'empreinte de ce dont l'image est faite : les dépendances et la recette.

    Sans ça, une dépendance ajoutée au dépôt laisserait tourner l'image d'hier — un vert rendu sur
    un environnement que personne n'a plus. Ici, l'image manque, donc elle est reconstruite, et
    personne n'a à s'en souvenir.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")

    def etiquette(**reglages: str) -> str:
        clone.journal.unlink(missing_ok=True)
        clone.lance("--only", "pytest", MAESTRO_FAUX_DOCKER_INSPECT_CODE="1", **reglages)
        return etiquette_image(clone.appels())

    depart = etiquette()
    clone.modifie("pyproject.toml", '\n[tool.rien]\nvaleur = "1"\n')
    apres_dependances = etiquette()
    clone.modifie("scripts/ci/pytest.Dockerfile", "\n# une couche de plus\n")
    apres_recette = etiquette()

    assert depart != apres_dependances, "une dépendance qui change doit changer l'image"
    assert apres_dependances != apres_recette, "une recette qui change doit changer l'image"
    assert depart.startswith("maestro-pytest:"), depart


def test_le_verdict_dit_toujours_dans_quel_regime_pytest_a_joue(clone: Clone) -> None:
    """Dans les DEUX sens : « conteneur » n'est pas plus tacite que « natif ».

    Le lecteur du résumé n'a aucun autre moyen de le savoir — et c'est ce qui décide de la valeur
    du vert qu'il lit.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.modifie("scripts/gitlab/lib.sh", "echo encore\n")

    dedans = clone.lance("--only", "pytest")
    assert "conteneur" in ligne_du_job(dedans.stdout, "pytest").lower()

    dehors = clone.lance("--natif", "--only", "pytest")
    assert "NATIF" in ligne_du_job(dehors.stdout, "pytest")


def test_le_dockerfile_porte_git_sans_identite_globale() -> None:
    """Les deux moitiés indissociables de #333, sur le VRAI Dockerfile du dépôt.

    Sept modules d'outillage sont gardés par `skipif(shutil.which("git") is None)` : sans git,
    l'image sauterait 285 tests en rendant un vert que rien ne distinguerait d'un vert complet.
    Mais git doit venir de l'IMAGE PLEINE et non d'un `apt-get install` — celui-ci met une
    dépendance aux miroirs Debian sur le chemin critique (le pipeline de !269 est mort dessus) —,
    et SANS identité globale, sous peine de remasquer le bug que #332 a trouvé : la fusion de
    `maestro/projets/application.py` n'échouait que sur les machines sans `~/.gitconfig`.
    """
    recette = (RACINE / "scripts" / "ci" / "pytest.Dockerfile").read_text(encoding="utf-8")
    lignes = [ligne.strip() for ligne in recette.splitlines() if not ligne.strip().startswith("#")]
    instructions = "\n".join(lignes)

    assert re.search(r"^FROM python:3\.11\s*$", instructions, re.MULTILINE), \
        "l'image PLEINE porte git ; `-slim` ne l'a pas, et l'installer coûte une panne récurrente"
    assert not re.search(r"apt-get\s+install[^\n]*\bgit\b", instructions), \
        "git par apt-get met les miroirs Debian sur le chemin critique de chaque build (#333)"
    assert not re.search(r"git config\s+--global\s+user\.", instructions), \
        "une identité git globale remasquerait le bug de production trouvé par #332"


# --- Le lanceur d'itération serrée (#405) ---------------------------------------------------------
# `local.sh` rend un VERDICT sur le périmètre du diff ; `pytest.sh` fait ITÉRER sur une cible. Deux
# questions différentes, mais la même réponse sur le régime — et c'est tout l'enjeu de ce bloc.
#
# La panne qu'il ferme : le conteneur de #372 n'était joignable QUE par le périmètre déduit du diff,
# donc viser une suite retombait sur un `python -m pytest` natif. Mesuré le 2026-08-21 sur
# `tests/test_cycle_de_vie.py` : ~8 min en natif contre 21 s dans le conteneur (×18).
#
# Comme le bloc précédent, ces tests n'ouvrent AUCUN conteneur : SHIM_DOCKER répond, et ce sont les
# DÉCISIONS du lanceur qu'on lit dans son journal.

#: Les fonctions qui décident OÙ et COMMENT pytest joue. Chacune ne doit être DÉFINIE qu'une fois,
#: dans `pytest-regime.sh` — c'est la forme vérifiable de « partagée, pas recopiée ».
PLOMBERIE_PARTAGEE = (
    "docker_repond",
    "regime_pytest_pressenti",
    "pytest_image",
    "pytest_image_construit",
    "pytest_conteneur",
    "choisit_regime_pytest",
    "workers_pytest",
    "coeurs_logiques",
    "xdist_installe",
    "verifie_venv_natif",
    "sonde_maestro",
    "venv_bin",
    "chemin_natif",
    "image_docker_disponible",
)


def definitions(texte: str, fonction: str) -> int:
    """Combien de fois ce texte DÉFINIT cette fonction (un appel n'est pas une définition)."""
    return len(re.findall(rf"^{re.escape(fonction)}\(\)\s*\{{", texte, re.MULTILINE))


def test_le_detecteur_de_definition_distingue_une_definition_d_un_appel() -> None:
    """Le motif prouve ce qu'il cherche AVANT de balayer — sinon le test suivant est un ✓ vide.

    Un compteur qui rendrait 0 partout ferait passer « aucune duplication » pour un fait alors
    qu'il ne dit que « je ne sais pas lire une définition ». On lui montre donc les trois formes
    qu'il doit distinguer.
    """
    assert definitions("pytest_conteneur() {\n  :\n}\n", "pytest_conteneur") == 1
    assert definitions('  pytest_conteneur "$IMAGE" -q\n', "pytest_conteneur") == 0, \
        "un APPEL n'est pas une définition"
    assert definitions("pytest_conteneur() {\n:\n}\npytest_conteneur() {\n:\n}\n",
                       "pytest_conteneur") == 2, "une recopie doit se voir"


def test_la_plomberie_du_conteneur_n_est_definie_qu_une_seule_fois() -> None:
    """« Partagée, pas recopiée » (#405) — l'invariant central du ticket, rendu vérifiable.

    Deux plomberies à tenir d'accord seraient le premier moyen de rendre un vert sur une forme que
    l'autre a corrigée depuis : le filet cesserait de prédire ce que le lanceur exécute, et « ça
    passe chez moi » redeviendrait une phrase qu'on peut dire depuis deux endroits.
    """
    fichiers = {
        nom: (RACINE / "scripts" / "ci" / nom).read_text(encoding="utf-8")
        for nom in ("local.sh", "pytest-regime.sh", "pytest.sh")
    }
    for fonction in PLOMBERIE_PARTAGEE:
        porteurs = {nom: definitions(texte, fonction) for nom, texte in fichiers.items()}
        assert sum(porteurs.values()) == 1, \
            f"`{fonction}` doit être définie une seule fois, trouvée : {porteurs}"
        assert porteurs["pytest-regime.sh"] == 1, \
            f"`{fonction}` doit vivre dans la bibliothèque partagée, trouvée : {porteurs}"


def test_les_deux_appelants_sourcent_la_meme_bibliotheque() -> None:
    """Le partage passe par un `source`, jamais par une copie du fichier au moment du build."""
    for nom in ("local.sh", "pytest.sh"):
        texte = (RACINE / "scripts" / "ci" / nom).read_text(encoding="utf-8")
        assert re.search(r"^\.\s+\"\$RACINE/scripts/ci/pytest-regime\.sh\"", texte, re.MULTILINE), \
            f"{nom} doit sourcer scripts/ci/pytest-regime.sh"


def test_le_lanceur_joue_dans_le_conteneur_ce_qu_on_lui_passe(clone: Clone) -> None:
    """Le cœur du ticket : une CIBLE, pas un périmètre déduit du diff."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    acheve = clone.lance_pytest("tests/test_horloge.py", "-q")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    runs = lancements_conteneur(clone.appels())
    assert len(runs) == 1, f"un seul conteneur attendu, reçu : {runs}"
    assert not lancements_pytest(clone.appels()), \
        "le venv du poste ne doit pas jouer la suite quand le conteneur le fait"
    # Les arguments passent TELS QUELS — le lanceur n'a pas d'opinion sur ce qu'on veut jouer.
    assert "tests/test_horloge.py" in runs[0]
    assert " -q" in runs[0]
    # …et le dépôt est monté exactement comme pour le filet, deux niveaux sous la racine (#372).
    montage = cible_du_montage(runs[0])
    assert montage.count("/") >= 2, \
        f"le dépôt ne doit jamais être monté à la racine du conteneur, reçu : {montage}"
    assert f" -w {montage} " in runs[0]
    assert f"PYTHONPATH={montage}" in runs[0]


def test_le_lanceur_annonce_toujours_ou_il_a_joue(clone: Clone) -> None:
    """Un lanceur vingt fois plus rapide qui tait où il a joué est un faux vert en puissance."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    acheve = clone.lance_pytest("tests/test_horloge.py")

    assert "conteneur Linux" in acheve.stdout, acheve.stdout + acheve.stderr


def test_le_lanceur_sans_docker_retombe_en_natif_et_le_dit(clone: Clone) -> None:
    """Le repli est ANNONCÉ, jamais silencieux — et il nomme sa raison.

    Sur une suite d'outillage l'écart est d'un facteur vingt : qui ne sait pas qu'il est retombé en
    natif croit simplement que sa suite est lente, et n'ira jamais réveiller son démon Docker.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    acheve = clone.lance_pytest("tests/test_horloge.py", MAESTRO_FAUX_DOCKER_VERSION_CODE="1")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not lancements_conteneur(clone.appels())
    lances = lancements_pytest(clone.appels())
    assert len(lances) == 1, f"le venv du poste devait jouer la suite, reçu : {clone.appels()}"
    assert "tests/test_horloge.py" in lances[0]
    sortie = acheve.stdout + acheve.stderr
    assert "NATIF" in sortie, sortie
    assert "Docker" in sortie, "le repli doit nommer ce qui manque"


def test_le_lanceur_en_regime_conteneur_exige_echoue_au_lieu_de_retomber(clone: Clone) -> None:
    """`--conteneur` est une EXIGENCE : un repli silencieux ferait passer « Docker manquait » pour
    « tout va bien » — exactement ce que `local.sh --conteneur` refuse déjà."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    acheve = clone.lance_pytest(
        "--conteneur", "tests/test_horloge.py", MAESTRO_FAUX_DOCKER_VERSION_CODE="1"
    )

    assert acheve.returncode != 0, acheve.stdout + acheve.stderr
    assert not lancements_pytest(clone.appels()), "rien ne doit être joué en repli"
    assert not lancements_conteneur(clone.appels())


def test_le_lanceur_force_le_venv_du_poste_avec_natif(clone: Clone) -> None:
    """`--natif` ne SONDE même pas Docker : c'est l'ancien régime, demandé en toutes lettres."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    acheve = clone.lance_pytest("--natif", "tests/test_horloge.py")

    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not lancements_conteneur(clone.appels())
    assert len(lancements_pytest(clone.appels())) == 1


def test_le_lanceur_ajoute_le_parallelisme_d_office_dans_le_conteneur(clone: Clone) -> None:
    """C'est l'essentiel du gain : 1 min 51 sans xdist contre 21 s avec, sur la même suite."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.lance_pytest("tests/test_horloge.py")

    runs = lancements_conteneur(clone.appels())
    assert re.search(r" -n \S+", runs[0]), f"parallélisme attendu dans : {runs[0]}"


def test_le_lanceur_n_impose_jamais_le_parallelisme_en_natif(clone: Clone) -> None:
    """En natif, démarrer les workers coûte plus qu'une suite applicative ciblée.

    C'est la règle déjà écrite pour le filet (~5,5 s de démarrage), et elle a été mesurée ici :
    `tests/test_engine.py` fait 6,3 s en série contre 37,5 s à `-n 8` — le parallélisme d'office
    rendait SIX FOIS plus lent le cas même qu'il devait servir. Une suite d'outillage assez grosse
    pour rentabiliser des workers est de toute façon celle qu'il faut jouer dans le conteneur.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.lance_pytest("--natif", "tests/test_horloge.py")

    lances = lancements_pytest(clone.appels())
    assert lances, clone.appels()
    assert not re.findall(r" -n (\S+)", lances[0]), \
        f"le lanceur a imposé des workers en natif : {lances[0]}"


@pytest.mark.parametrize(
    "argument",
    [
        # Un choix déjà fait : le sien gagne, toujours.
        "-n2",
        "--numprocesses=3",
        # Une intention de REGARDER tourner : xdist capture et entrelace la sortie par worker, ce
        # qui vide ces options de leur sens. pytest ne s'en plaint pas — il les rend inutiles, ce
        # qui est pire qu'une erreur franche.
        "--pdb",
        "-s",
        "--capture=no",
    ],
)
def test_le_lanceur_n_impose_jamais_le_parallelisme_contre_un_choix_explicite(
    clone: Clone, argument: str
) -> None:
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.lance_pytest("tests/test_horloge.py", argument)

    runs = lancements_conteneur(clone.appels())
    ajoutes = re.findall(r" -n (\S+)", runs[0])
    assert not ajoutes, f"le lanceur a imposé -n {ajoutes} malgré {argument!r} : {runs[0]}"


def test_le_lanceur_respecte_p_no_xdist_qui_voyage_en_deux_jetons(clone: Clone) -> None:
    """`-p no:xdist` s'écrit en DEUX arguments : reconnaître `-p` seul désarmerait le parallélisme
    pour tout autre plugin chargé de la même façon."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    clone.lance_pytest("tests/test_horloge.py", "-p", "no:xdist")

    runs = lancements_conteneur(clone.appels())
    assert not re.findall(r" -n (\S+)", runs[0]), runs[0]


def test_le_lanceur_sans_argument_rend_l_aide_et_ne_joue_rien(clone: Clone) -> None:
    """Sans cible, pytest ramasserait TOUTE la suite : une collecte surprise qui se paie en
    minutes. Le lanceur d'itération demande ce qu'on veut jouer."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    acheve = clone.lance_pytest()

    assert acheve.returncode != 0
    assert "pytest.sh" in acheve.stdout, acheve.stdout + acheve.stderr
    assert not lancements_conteneur(clone.appels())
    assert not lancements_pytest(clone.appels())


def test_le_lanceur_rend_le_code_de_sortie_de_pytest(clone: Clone) -> None:
    """Un lanceur qui avalerait le rouge de pytest serait pire qu'inutile."""
    clone.equipe_tout()
    clone.equipe_conteneur()
    acheve = clone.lance_pytest("tests/test_horloge.py", MAESTRO_FAUX_DOCKER_CODE="1")

    assert acheve.returncode == 1, acheve.stdout + acheve.stderr


def test_le_lanceur_herite_du_garde_fou_du_venv_partage(clone: Clone) -> None:
    """En natif, `import maestro` doit se résoudre ICI — garde-fou de #194, hérité et non recopié.

    Dans un worktree, le venv partagé installe `maestro` en éditable POINTÉ SUR LE CLONE PRINCIPAL :
    sans ce contrôle, le lanceur testerait la branche du voisin en le disant vert.
    """
    clone.equipe_tout()
    clone.equipe_conteneur()
    acheve = clone.lance_pytest(
        "--natif",
        "tests/test_horloge.py",
        MAESTRO_FAUX_SONDE_SORTIE="AILLEURS /ailleurs/maestro\\n",
    )

    assert acheve.returncode != 0, acheve.stdout + acheve.stderr
    assert not lancements_pytest(clone.appels()), "rien ne doit être joué sur un venv qui ment"
    assert "AILLEURS" in acheve.stdout + acheve.stderr or \
        "ailleurs" in (acheve.stdout + acheve.stderr).lower()


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
    assert IMAGE in runs[0]                     # l'image vient de MAESTRO_SHELLCHECK_IMAGE
    assert "scripts/gitlab/lib.sh" in runs[0]   # les fichiers sont les arguments de la boucle
    assert "for fichier in" in runs[0]          # …et la boucle est bien à l'intérieur


def test_le_pipeline_decoupe_shellcheck_comme_le_filet() -> None:
    """L'invariant qui protège du filet menteur — sur les fichiers VERSIONNÉS, pas le dépôt jetable.

    shellcheck ne suit un `# shellcheck source=` que si le fichier sourcé est lui aussi sur la
    ligne de commande. Un côté découpé et l'autre groupé, c'est donc un filet plus strict que la CI
    qu'il prédit : rouge en local, vert en pipeline, sur des remarques que rien n'explique.
    """
    pipeline = (RACINE / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    filet = (RACINE / "scripts" / "ci" / "local.sh").read_text(encoding="utf-8")
    # Le pipeline boucle sur ses fichiers au lieu de les passer tous d'un coup.
    assert re.search(r"for fichier in \$files", pipeline), \
        "le job shellcheck du pipeline doit boucler fichier par fichier (#285)"
    assert not re.search(r"shellcheck --severity=\w+ \$files", pipeline), \
        "l'appel groupé du pipeline ferait diverger son verdict de celui du filet"
    # Et aucun des deux ne passe `-x` : il rétablirait le lien entre fichiers, mais ferait
    # ré-analyser lib.sh par chacun des scripts qui la sourcent (34 s au lieu de 12).
    for source, nom in ((pipeline, ".github/workflows/ci.yml"), (filet, "scripts/ci/local.sh")):
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
    assert IMAGE in ligne          # …et le conseil nomme bien l'image de repli


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


#: Un chemin de journal CITÉ dans la sortie, sous la forme où le filet l'imprime. `[^\s)]` borne le
#: jeton sans avaler la parenthèse de « (suite dans …) ».
CITATION_JOURNAL = re.compile(r"[^\s)]*ci-local[^\s)]*\.log")


def test_le_journal_d_un_job_rouge_est_sous_la_racine_et_cite_en_relatif(clone: Clone) -> None:
    """Le cœur de #234 : le chemin imprimé se lit tel quel depuis le répertoire de travail.

    Ce qui est exigé porte sur les chemins **cités pour lecture**, et sur eux seuls. La bannière
    « Filet CI local — <racine> » nomme légitimement le dépôt vérifié : c'est un titre, pas une
    invitation à ouvrir un fichier, et une session est déjà DANS ce répertoire.

    L'assertion d'origine (« la racine n'apparaît nulle part ») confondait les deux, et ne s'en est
    jamais aperçue : elle comparait le `E:\\…\\clone` de Python au `/e/…/clone` du script bash, deux
    écritures du même dossier qui ne peuvent pas se contenir. Elle passait donc quoi qu'il arrive
    sous Windows — le seul poste qui la jouait — et tombait dès qu'un vrai Linux la rendait
    comparable (#333). On vérifie donc la FORME de chaque citation, ce qu'aucune plateforme ne
    rend vacuellement vrai.
    """
    clone.equipe_tout()
    acheve = clone.lance("--only", "mypy", MAESTRO_FAUX_MYPY_CODE="1",
                         MAESTRO_FAUX_MYPY_SORTIE=SORTIE_LONGUE)

    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert (journal_ci(clone) / "mypy.log").is_file(), "le journal doit vivre sous la racine"
    assert "journal : .maestro/ci-local/mypy.log" in acheve.stdout
    assert "(suite dans .maestro/ci-local/mypy.log)" in acheve.stdout

    citations = CITATION_JOURNAL.findall(acheve.stdout)
    assert citations, "un job rouge doit citer son journal — sinon la raison de l'échec est perdue"
    assert set(citations) == {".maestro/ci-local/mypy.log"}, (
        "tout chemin cité pour lecture doit être relatif au répertoire de travail — un absolu "
        f"demande une approbation que personne n'est là pour donner : {sorted(set(citations))}"
    )
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
    clone.pose_npm(
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
    pipeline = (RACINE / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "npm run typecheck" in pipeline, "un script jamais joué en CI finit par ne plus passer"


# --- Git est une dépendance de la suite, pas un confort (#333) ------------------------------------
# Sept modules d'outillage montent un vrai dépôt jetable et sont gardés par
# `skipif(shutil.which("git") is None)` : ~285 tests SAUTÉS en silence, pipeline verte, partout où
# git manque. C'est arrivé pendant toute la vie de la CI GitLab, dont le job `pytest` tournait dans
# `python:3.11-slim` — si bien que ces tests n'ont jamais tourné que sur des postes de développement
# (tous sous Windows), et que le premier runner Linux muni de git en a trouvé 16 rouges d'un coup
# (#332). Les deux moitiés du remède se gardent ici : git disponible dans le job, et son absence en
# CI rendue bruyante (le garde-fou de `tests/conftest.py`).


def test_le_job_pytest_dispose_de_git() -> None:
    """Sans git dans ce job, ~285 tests redeviennent invisibles — et la pipeline reste verte.

    Le fichier VERSIONNÉ : c'est le pipeline réel qui décide de ce qui est vérifié, et l'angle mort
    ne se voit dans aucun compte rendu — un job qui saute 285 tests et un job qui les joue tous
    rendent le même « vert ».

    Sur GitHub Actions, git vient AVEC le runner hébergé : c'est même lui qui fait le checkout. Le
    seul moyen de le reperdre est de renvoyer le job dans un conteneur (`container:`), ce qui
    ramènerait exactement l'image slim de la CI GitLab. C'est donc cela qui est épinglé, plutôt
    qu'une recette d'installation qui n'a plus lieu d'être — et rappelons pourquoi elle n'en était
    pas une : un `apt-get install git` au lancement met une dépendance réseau sur les miroirs
    Debian dans CHAQUE pipeline, donc sur le chemin critique du merge (le pipeline de !269 est mort
    dessus, « Unable to connect to deb.debian.org », avant même que pytest démarre).
    """
    pipeline = (RACINE / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = pipeline.split("\n  pytest:", 1)
    assert len(job) == 2, "le job `pytest` a été renommé — ce test doit suivre"
    corps = job[1].split("\n  mypy:", 1)[0]

    lignes = [ligne.strip() for ligne in corps.splitlines()]
    assert any(ligne.startswith("runs-on:") for ligne in lignes), \
        "le job `pytest` doit nommer son runner"
    conteneur = next((ligne for ligne in lignes if ligne.startswith("container:")), None)
    installe = any("install" in ligne and " git" in ligne for ligne in lignes)

    assert conteneur is None or installe, (
        f"« {conteneur} » renvoie le job dans un conteneur, qui n'a pas forcément git : la "
        "suite en dépend pour ~285 tests d'outillage, sautés en silence sans lui (#333)"
    )


def test_git_absent_en_ci_est_une_erreur_et_non_un_saut() -> None:
    """La règle est une CONJONCTION : c'est « en CI **et** sans git » qui est fautif.

    Un poste sans git a le droit de sauter — il dit alors « cette machine ne peut pas répondre ».
    En CI le même saut dit « rien n'a été vérifié » avec les mots de « tout va bien », et c'est
    cette confusion que #333 supprime.
    """
    from conftest import git_manquant_en_ci  # le conftest du dossier, sur le sys.path de pytest

    assert git_manquant_en_ci({"CI": "true"}, None)
    assert git_manquant_en_ci({"GITHUB_ACTIONS": "true"}, None)
    # Les trois situations acceptables, dont aucune ne doit arrêter la suite.
    assert not git_manquant_en_ci({"CI": "true"}, "/usr/bin/git")
    assert not git_manquant_en_ci({}, None)
    assert not git_manquant_en_ci({"CI": ""}, None), "une variable vide n'est pas une CI"


# --- Le filet est la source UNIQUE des contrôles locaux (#310) ------------------------------------
# Une commande de `.claude/` qui recopie la recette d'un job la fige : `/mr-fix` a prescrit jusqu'à
# #310 une table de miroirs avec `pytest -n auto` sur la suite entière — l'inverse exact de ce que
# CLAUDE.md impose depuis #214, et payé au pire moment, en plein diagnostic d'un pipeline rouge
# (~10 min contre ~40 s). Le texte que la session lit en dernier est celui qui l'emporte sur la
# règle générale : c'est donc ici, dans les prompts, que la contradiction se garde.

OUTILS_CI = ("pytest", "ruff", "mypy", "shellcheck")


def prescriptions_des_prompts() -> list[tuple[str, int, str]]:
    """Les endroits d'un prompt qui font JOUER quelque chose : blocs de code et cellules de tableau.

    Même parti pris que les tests de prompts de `test_collaboration.py` (#196, #233) : la prose a le
    droit — le devoir, même — de nommer une forme pour dire de ne pas l'employer (`/mr-fix` proscrit
    désormais `pytest -n auto` en toutes lettres). Ce qui prescrit, c'est le bloc qu'on recopie et
    la table qu'on suit.
    """
    prescriptions: list[tuple[str, int, str]] = []
    for prompt in sorted((RACINE / ".claude").rglob("*.md")):
        dans_bloc = False
        for numero, ligne in enumerate(prompt.read_text(encoding="utf-8").splitlines(), 1):
            nue = ligne.strip()
            if nue.startswith("```"):
                dans_bloc = not dans_bloc
                continue
            if dans_bloc or nue.startswith("|"):
                prescriptions.append((prompt.relative_to(RACINE).as_posix(), numero, nue))
    return prescriptions


def prescrit_sa_propre_recette(nue: str) -> bool:
    """Une seule échappatoire hors du filet : viser LA suite rouge (docs/10 §8.4, 1,5 s).

    Elle se vérifie sur la ligne entière, et non à la première mention d'un chemin de suite : la
    table supprimée par #310 nommait les deux — `pytest -n auto`, « ou `… tests/test_<suite>.py`
    pour reproduire le seul test rouge » —, si bien qu'un simple `"tests/test_" in nue` aurait
    laissé passer la régression que ce test existe pour attraper. Parler de parallélisme ou de
    couverture, c'est reparler de la suite entière.
    """
    if "ci/local.sh" in nue or not any(outil in nue for outil in OUTILS_CI):
        return False
    return not ("tests/test_" in nue and "-n " not in nue and "--cov" not in nue)


def test_aucun_prompt_ne_recopie_la_recette_d_un_job_ci() -> None:
    fautives = [
        f"{chemin}:{numero}: {nue}"
        for chemin, numero, nue in prescriptions_des_prompts()
        if prescrit_sa_propre_recette(nue)
    ]
    assert fautives == [], (
        "un prompt réinvente la recette d'un job CI (#310) — renvoyer à scripts/ci/local.sh :\n"
        + "\n".join(fautives)
    )


def test_le_garde_fou_attrape_la_table_que_310_a_supprimee() -> None:
    """Un garde-fou qui ne mord pas est pire qu'absent : on épingle les lignes RÉELLEMENT retirées,
    et ce qui doit leur survivre."""
    assert prescrit_sa_propre_recette(
        "| `pytest` | `<venv-python> -m pytest -n auto` (ou `… -m pytest tests/test_<suite>.py` "
        "pour reproduire le seul test rouge de la trace) |"
    )
    assert prescrit_sa_propre_recette("| `mypy` | `<venv-python> -m mypy maestro` |")
    assert not prescrit_sa_propre_recette("bash scripts/ci/local.sh --only pytest")
    assert not prescrit_sa_propre_recette("<venv-python> -m pytest tests/test_engine.py")


def test_les_commandes_qui_verifient_en_local_renvoient_au_filet() -> None:
    """Le pendant POSITIF, et la leçon de !198 : retirer une recette ne sert à rien si rien ne
    renvoie au script qui la porte — le prompt dirait alors de vérifier, sans dire avec quoi."""
    for nom in ("mr-fix.md", "ticket-finish.md"):
        texte = (RACINE / ".claude" / "commands" / nom).read_text(encoding="utf-8")
        assert "scripts/ci/local.sh" in texte, \
            f"/{nom[:-3]} ne renvoie plus au filet CI local (#310)"


def test_le_filet_est_autorise_dans_les_DEUX_regimes() -> None:
    """Une commande prescrite mais non autorisée, c'est un refus de permission par ticket (§11.7).

    Les deux fichiers, parce qu'ils servent deux régimes : `.claude/settings.json` la session
    interactive, `settings.run.json` la session autonome — dont l'`allow` est l'UNION des deux, mais
    qui ne peut compter sur personne pour accorder ce qui manque.
    """
    for chemin in (RACINE / ".claude" / "settings.json",
                   RACINE / "scripts" / "orchestrate" / "settings.run.json"):
        allow = json.loads(chemin.read_text(encoding="utf-8"))["permissions"]["allow"]
        assert "Bash(bash scripts/ci/local.sh:*)" in allow, \
            f"{chemin.name} n'autorise pas le filet, que deux commandes prescrivent (#310)"
