"""Les tables de l'analyse et les lecteurs de manifestes (#1030).

Tout ce que ce module sait du monde est **écrit en table** : quelle extension
est quel langage, quel fichier prouve quel gestionnaire, quel marqueur est
quelle CI, quel hôte est quelle forge. C'est délibéré — la matière de l'analyse
bouge (un gestionnaire naît, une CI meurt), et une table se relit et se corrige
là où une cascade de `if` se réécrit.

**Aucune exécution, jamais.** Ce module n'importe pas `subprocess` et n'en
importera pas : toute la détection se fait en **lisant des fichiers**, y compris
là où lancer la commande serait plus simple (`npm run`, `git remote -v`). C'est
la première promesse du ticket, et elle ne tient que si elle est structurelle :
un projet qu'on analyse est du code de quelqu'un d'autre, et l'ouvrir ne doit
jamais revenir à le faire tourner.

**Rien n'est deviné en silence.** Chaque commande rendue porte son `origine`
(cf. `maestro.outillage.modele.ORIGINES_COMMANDE`) : `declaree` quand le projet
l'écrit — un `scripts.test` de `package.json`, une cible de `Makefile` —,
`convention` quand c'est la commande usuelle du gestionnaire détecté et que le
projet ne la mentionne nulle part. Les deux sont utiles ; les confondre ferait
passer une supposition pour une lecture.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from maestro.outillage.modele import Commande, Piece

#: Les dossiers que le parcours **ne descend jamais**. Dépendances installées,
#: sorties de compilation, caches d'outils : ils pèsent l'essentiel d'un projet
#: réel et n'apprennent rien sur lui — un `node_modules` consommerait à lui seul
#: le budget de fichiers, et l'analyse rendrait « JavaScript : 99 % » sur un
#: projet Python qui a un site de documentation.
#:
#: `.git` en fait partie, et c'est une **exception nommée** plutôt qu'un oubli :
#: le VCS et la forge s'y lisent, mais par deux fichiers appelés par leur nom
#: (`detecter_vcs`, qui lit `HEAD` et `config`), jamais en énumérant les objets.
#:
#: `.maestro` aussi : c'est l'atelier de Maestro dans la racine (docs/24 §2.4),
#: pas le projet. L'analyser reviendrait à recommander un outillage d'après les
#: traces du précédent.
IGNORES_DEFAUT: tuple[str, ...] = (
    ".bundle",
    ".cache",
    ".git",
    ".gradle",
    ".idea",
    ".maestro",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".parcel-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svelte-kit",
    ".terraform",
    ".tox",
    ".venv",
    "__pycache__",
    "bower_components",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "target",
    "venv",
    "vendor",
)

#: Extension → langage. Volontairement bornée aux langages de **code** : les
#: `.md`, `.json` et `.yml` sont présents partout et noieraient la part des
#: autres, alors qu'ils ne disent rien de ce qu'on construit ou teste. Ce que le
#: projet écrit en Markdown se constate ailleurs — dans ses conventions.
LANGAGE_PAR_EXTENSION: dict[str, str] = {
    ".c": "C",
    ".cc": "C++",
    ".clj": "Clojure",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".dart": "Dart",
    ".erl": "Erlang",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".fs": "F#",
    ".go": "Go",
    ".h": "C",
    ".hpp": "C++",
    ".hs": "Haskell",
    ".java": "Java",
    ".jl": "Julia",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".lua": "Lua",
    ".m": "Objective-C",
    ".mjs": "JavaScript",
    ".php": "PHP",
    ".pl": "Perl",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".r": "R",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scala": "Scala",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".sql": "SQL",
    ".svelte": "Svelte",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".zig": "Zig",
}

#: Fichier marqueur → nom du gestionnaire. L'ordre n'a pas d'importance ici :
#: c'est la présence du fichier qui décide, et un projet polyglotte en déclare
#: légitimement plusieurs.
GESTIONNAIRE_PAR_MARQUEUR: dict[str, str] = {
    "Cargo.toml": "cargo",
    "CMakeLists.txt": "cmake",
    "Gemfile": "bundler",
    "Makefile": "make",
    "Pipfile": "pipenv",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "composer.json": "composer",
    "go.mod": "go",
    "mix.exs": "mix",
    "package.json": "npm",
    "pom.xml": "maven",
    "pubspec.yaml": "pub",
    "pyproject.toml": "pip",
    "requirements.txt": "pip",
}

#: Verrou → gestionnaire Node réel. `package.json` ne dit pas *qui* installe :
#: c'est le verrou posé à côté qui tranche, et c'est lui qui décide aussi entre
#: `npm ci` (verrou présent) et `npm install`.
VERROU_NODE: dict[str, str] = {
    "bun.lock": "bun",
    "bun.lockb": "bun",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
}

#: Comment chaque gestionnaire Node installe et joue un script du manifeste.
NODE_COMMANDES: dict[str, tuple[str, str]] = {
    "bun": ("bun install", "bun run {nom}"),
    "npm": ("npm install", "npm run {nom}"),
    "pnpm": ("pnpm install", "pnpm run {nom}"),
    "yarn": ("yarn install", "yarn {nom}"),
}

#: Verrou → gestionnaire Python. Même logique que pour Node : `pyproject.toml`
#: seul ne dit pas qui installe.
VERROU_PYTHON: dict[str, str] = {
    "poetry.lock": "poetry",
    "uv.lock": "uv",
}

#: Usage → noms de scripts à chercher dans un manifeste, **par ordre de
#: préférence**. Le premier nom présent gagne : deux analyses du même projet
#: rendent donc la même commande, ce qu'un parcours du dictionnaire des scripts
#: ne garantirait pas.
SCRIPTS_PAR_USAGE: dict[str, tuple[str, ...]] = {
    "construire": ("build", "compile", "bundle", "dist"),
    "tester": ("test", "tests", "test:unit", "unit", "spec"),
    "lint": ("lint", "eslint", "check"),
    "formater": ("format", "fmt", "prettier"),
    "types": ("typecheck", "type-check", "types", "tsc"),
    "demarrer": ("dev", "start", "serve"),
}

#: Usage → noms de cibles `Makefile`, même règle de préférence.
CIBLES_MAKE_PAR_USAGE: dict[str, tuple[str, ...]] = {
    "installer": ("install", "setup", "deps", "bootstrap"),
    "construire": ("build", "all", "compile"),
    "tester": ("test", "tests", "check"),
    "lint": ("lint",),
    "formater": ("format", "fmt"),
    "types": ("typecheck", "types"),
    "demarrer": ("run", "dev", "start", "serve"),
}

#: Table d'un outil Python détecté par sa section de `pyproject.toml`.
#: `ruff format` n'est proposé que si le projet configure le formateur : `ruff`
#: sert d'abord de linter, et proposer les deux d'office reviendrait à
#: recommander un formatage que le projet n'a pas choisi.
OUTILS_PYPROJECT: tuple[tuple[str, str, str], ...] = (
    ("tool.pytest", "tester", "pytest"),
    ("tool.ruff", "lint", "ruff check ."),
    ("tool.ruff.format", "formater", "ruff format ."),
    ("tool.black", "formater", "black ."),
    ("tool.mypy", "types", "mypy ."),
    ("tool.pyright", "types", "pyright"),
    ("tool.flake8", "lint", "flake8"),
)

#: Gestionnaire → commandes **conventionnelles** (usage, commande). Elles ne
#: sont proposées que si le projet n'en déclare pas pour le même usage, et elles
#: voyagent toujours en `origine="convention"` : ce sont les commandes de
#: l'outil, pas celles du projet.
CONVENTIONS_PAR_GESTIONNAIRE: dict[str, tuple[tuple[str, str], ...]] = {
    "bundler": (("installer", "bundle install"),),
    "cargo": (
        ("installer", "cargo fetch"),
        ("construire", "cargo build"),
        ("tester", "cargo test"),
        ("lint", "cargo clippy"),
        ("formater", "cargo fmt"),
    ),
    "cmake": (
        ("construire", "cmake -B build && cmake --build build"),
        ("tester", "ctest --test-dir build"),
    ),
    "composer": (("installer", "composer install"),),
    "go": (
        ("installer", "go mod download"),
        ("construire", "go build ./..."),
        ("tester", "go test ./..."),
        ("lint", "go vet ./..."),
        ("formater", "go fmt ./..."),
    ),
    "gradle": (
        ("construire", "./gradlew build"),
        ("tester", "./gradlew test"),
        ("lint", "./gradlew check"),
    ),
    "maven": (
        ("construire", "mvn -B package"),
        ("tester", "mvn -B test"),
    ),
    "mix": (
        ("installer", "mix deps.get"),
        ("construire", "mix compile"),
        ("tester", "mix test"),
        ("formater", "mix format"),
    ),
    "pub": (
        ("installer", "dart pub get"),
        ("tester", "dart test"),
        ("lint", "dart analyze"),
    ),
}

#: `(gestionnaire, marqueur)` → commande d'installation Python. Le **marqueur**
#: compte autant que le gestionnaire : `pip install -r requirements.txt` sur un
#: projet qui n'a qu'un `pyproject.toml` échouerait, et un outillage généré qui
#: ne marche pas est pire que pas d'outillage du tout. Mesuré sur ce dépôt le
#: 2026-09-20 — c'est exactement la commande que la première version proposait.
INSTALLATION_PYTHON: dict[tuple[str, str], str] = {
    ("pip", "pyproject.toml"): "pip install -e .",
    ("pip", "requirements.txt"): "pip install -r requirements.txt",
    ("pipenv", "Pipfile"): "pipenv install --dev",
    ("poetry", "pyproject.toml"): "poetry install",
    ("uv", "pyproject.toml"): "uv sync",
}

#: Fichier de configuration à la racine → la commande qu'il **déclare**. Poser
#: un `.pre-commit-config.yaml` ou un `.golangci.yml`, c'est dire comment le
#: projet se vérifie : la commande qui va avec n'est pas une supposition, c'est
#: la lecture du fichier. Chacune vaut `origine="declaree"`, contrairement aux
#: conventions de gestionnaire.
MARQUEURS_COMMANDE: dict[str, tuple[str, str]] = {
    ".golangci.yml": ("lint", "golangci-lint run"),
    ".pre-commit-config.yaml": ("lint", "pre-commit run --all-files"),
    ".rubocop.yml": ("lint", "rubocop"),
    "pytest.ini": ("tester", "pytest"),
}

#: Marqueur (fichier ou dossier, relatif à la racine) → nom de la CI.
CI_PAR_MARQUEUR: dict[str, str] = {
    ".circleci/config.yml": "CircleCI",
    ".drone.yml": "Drone",
    ".github/workflows": "GitHub Actions",
    ".gitlab-ci.yml": "GitLab CI",
    ".travis.yml": "Travis CI",
    ".woodpecker.yml": "Woodpecker",
    "Jenkinsfile": "Jenkins",
    "appveyor.yml": "AppVeyor",
    "azure-pipelines.yml": "Azure Pipelines",
    "bitbucket-pipelines.yml": "Bitbucket Pipelines",
}

#: Fichier de convention → ce qu'il porte. Ce sont les conventions **déjà
#: écrites** du ticket : ce qu'un agent qui arrive doit lire avant d'inventer
#: les siennes, et ce que la génération ne doit pas contredire.
CONVENTIONS_CONNUES: dict[str, str] = {
    ".editorconfig": "style",
    ".eslintrc.json": "style",
    ".pre-commit-config.yaml": "vérifications",
    ".prettierrc": "style",
    ".rubocop.yml": "style",
    "AGENTS.md": "instructions d'agent",
    "CHANGELOG.md": "historique",
    "CLAUDE.md": "instructions d'agent",
    "CODE_OF_CONDUCT.md": "communauté",
    "CONTRIBUTING.md": "contribution",
    "GEMINI.md": "instructions d'agent",
    "LICENSE": "licence",
    "README.md": "présentation",
    "eslint.config.js": "style",
}

#: Les mêmes, quand le projet les écrit sans extension ou autrement.
CONVENTIONS_ALIAS: dict[str, str] = {
    "CONTRIBUTING": "CONTRIBUTING.md",
    "CONTRIBUTING.rst": "CONTRIBUTING.md",
    "LICENSE.md": "LICENSE",
    "LICENSE.txt": "LICENSE",
    "README": "README.md",
    "README.rst": "README.md",
    "README.txt": "README.md",
}

#: Dossiers de scripts d'un projet, par ordre de préférence (docs/38 §3.4).
#: Maestro **constate** celui que le projet a déjà ; `scripts` n'est le défaut
#: que lorsqu'il n'en a aucun.
DOSSIERS_SCRIPTS: tuple[str, ...] = ("scripts", "bin", "tools")

#: Le manifeste d'outillage d'un projet (docs/38 §4.3), relatif à sa racine.
#: Écrit **une fois** : l'analyse le reconnaît comme une pièce présente (#1030),
#: le contexte d'un agent n'est lu que de lui (#1032) et #1033 l'écrira. Deux
#: orthographes de ce chemin donneraient un outillage généré ici et relu là.
CHEMIN_MANIFESTE = ".maestro/outillage/manifeste.json"

#: Où chercher des skills **déjà écrits**. `.agents/skills` est l'emplacement
#: retenu (docs/38 §3.3) ; les trois autres sont ceux que les clients lisent, et
#: un skill qui y vit est un skill que le projet porte — le redonner ailleurs
#: serait le dupliquer.
DOSSIERS_SKILLS: tuple[str, ...] = (
    ".agents/skills",
    ".claude/skills",
    ".github/skills",
    ".gemini/skills",
)

#: Hôte du distant Git → nom de la forge, comparé par **suffixe** (`ssh.github.com`
#: est GitHub).
FORGE_PAR_HOTE: tuple[tuple[str, str], ...] = (
    ("github.com", "GitHub"),
    ("gitlab.com", "GitLab"),
    ("bitbucket.org", "Bitbucket"),
    ("dev.azure.com", "Azure DevOps"),
    ("visualstudio.com", "Azure DevOps"),
    ("codeberg.org", "Codeberg"),
    ("git.sr.ht", "SourceHut"),
)

#: Premier segment de l'hôte → forge, pour les instances **auto-hébergées**
#: (`gitlab.interne.example`, `gitea.chez-nous.fr`). C'est le cas le plus
#: courant en entreprise, et le rendre « inconnu » ferait manquer la forge d'un
#: projet sur deux. Le segment est un indice, pas une preuve : il ne sert que
#: lorsque l'hôte complet n'a rien donné.
FORGE_PAR_SEGMENT: dict[str, str] = {
    "bitbucket": "Bitbucket",
    "codeberg": "Codeberg",
    "forgejo": "Forgejo",
    "gitea": "Gitea",
    "github": "GitHub",
    "gitlab": "GitLab",
}

#: Ce qu'un nom de fichier de script laisse deviner de son usage. Sert à
#: **reconnaître** un script existant, jamais à en écrire un.
USAGE_PAR_NOM_DE_SCRIPT: dict[str, str] = {
    "bootstrap": "installer",
    "build": "construire",
    "check": "lint",
    "dev": "demarrer",
    "fmt": "formater",
    "format": "formater",
    "install": "installer",
    "lint": "lint",
    "run": "demarrer",
    "setup": "installer",
    "start": "demarrer",
    "test": "tester",
    "tests": "tester",
    "typecheck": "types",
}

#: Extensions d'un fichier qu'on tient pour un script exécutable. Le bit `+x`
#: n'existe pas sous Windows — le poste sur lequel Maestro tourne aujourd'hui —
#: et s'y fier ferait rendre une liste vide sur la moitié des postes.
EXTENSIONS_SCRIPT: frozenset[str] = frozenset({".sh", ".ps1", ".py", ".js", ".mjs", ".bat", ""})

_CIBLE_MAKE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*:(?!=)")
_HOTE_GIT = re.compile(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)")


def lire_texte(chemin: Path, octets_max: int) -> str:
    """Le contenu de `chemin`, plafonné à `octets_max` — jamais une exception.

    Un fichier illisible (droits, lien mort, disparu entre l'énumération et la
    lecture) rend la chaîne vide : l'analyse **constate ce qu'elle peut** et ne
    s'arrête pas sur un fichier. Le décodage remplace les octets invalides
    plutôt que de lever — un manifeste en latin-1 reste analysable.
    """
    try:
        with chemin.open("rb") as flux:
            brut = flux.read(octets_max)
    except OSError:
        return ""
    return brut.decode("utf-8", errors="replace")


def commandes_node(
    manifeste: Path,
    chemin_relatif: str,
    gestionnaire: str,
    octets_max: int,
) -> tuple[Commande, ...]:
    """Les commandes déclarées par les `scripts` d'un `package.json`.

    Lues, jamais jouées : le manifeste est du JSON, et son champ `scripts` est
    un dictionnaire de chaînes. Un JSON illisible rend un tuple vide — un
    manifeste cassé est un fait du projet, pas une panne de l'analyse.
    """
    donnees = _json_ou_vide(lire_texte(manifeste, octets_max))
    scripts = donnees.get("scripts")
    if not isinstance(scripts, dict):
        return ()
    _, gabarit = NODE_COMMANDES.get(gestionnaire, NODE_COMMANDES["npm"])
    trouvees: list[Commande] = []
    for usage, candidats in SCRIPTS_PAR_USAGE.items():
        nom = next((c for c in candidats if isinstance(scripts.get(c), str)), None)
        if nom is None:
            continue
        trouvees.append(
            Commande(
                usage=usage,
                commande=gabarit.format(nom=nom),
                chemin=chemin_relatif,
                extrait=f"scripts.{nom}",
                origine="declaree",
            )
        )
    return tuple(trouvees)


def commandes_pyproject(
    manifeste: Path,
    chemin_relatif: str,
    octets_max: int,
) -> tuple[Commande, ...]:
    """Les outils Python **configurés** dans un `pyproject.toml`.

    Configurer `[tool.ruff]` est une déclaration : le projet dit qu'il se
    vérifie ainsi. La commande, elle, reste celle de l'outil — d'où un
    `extrait` qui nomme la section lue, et de quoi ouvrir le fichier pour
    vérifier.
    """
    try:
        donnees = tomllib.loads(lire_texte(manifeste, octets_max))
    except (tomllib.TOMLDecodeError, ValueError):
        return ()
    return tuple(
        Commande(
            usage=usage,
            commande=commande,
            chemin=chemin_relatif,
            extrait=f"[{section}]",
            origine="declaree",
        )
        for section, usage, commande in OUTILS_PYPROJECT
        if _section_toml(donnees, section) is not None
    )


def gestionnaire_python(manifeste: Path, chemin_relatif: str, octets_max: int) -> str | None:
    """`poetry` si le `pyproject.toml` le déclare, `None` sinon.

    Le verrou tranche en premier (`VERROU_PYTHON`) ; cette lecture-ci n'est là
    que pour le projet Poetry dont le verrou n'est pas versionné.
    """
    try:
        donnees = tomllib.loads(lire_texte(manifeste, octets_max))
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    return "poetry" if _section_toml(donnees, "tool.poetry") is not None else None


def commandes_make(
    makefile: Path,
    chemin_relatif: str,
    octets_max: int,
) -> tuple[Commande, ...]:
    """Les cibles d'un `Makefile` qui correspondent à un usage connu.

    Seul l'en-tête d'une règle est lu (`cible:`), jamais sa recette : ce qu'une
    recette contient est du code du projet, et l'analyse n'a aucune raison de
    l'interpréter — encore moins de le jouer.
    """
    cibles = {
        correspondance.group(1)
        for ligne in lire_texte(makefile, octets_max).splitlines()
        if (correspondance := _CIBLE_MAKE.match(ligne))
    }
    trouvees: list[Commande] = []
    for usage, candidats in CIBLES_MAKE_PAR_USAGE.items():
        nom = next((c for c in candidats if c in cibles), None)
        if nom is None:
            continue
        trouvees.append(
            Commande(
                usage=usage,
                commande=f"make {nom}",
                chemin=chemin_relatif,
                extrait=f"cible {nom}",
                origine="declaree",
            )
        )
    return tuple(trouvees)


def commandes_conventionnelles(gestionnaire: str, chemin_relatif: str) -> tuple[Commande, ...]:
    """Les commandes usuelles d'un gestionnaire, en `origine="convention"`.

    Le projet ne les écrit nulle part : c'est l'outil qui les définit, et c'est
    pourquoi elles ne remplacent jamais une commande déclarée — l'appelant les
    reconnaît à leur origine et peut les traiter comme des propositions.
    """
    return tuple(
        Commande(
            usage=usage,
            commande=commande,
            chemin=chemin_relatif,
            extrait=f"convention {gestionnaire}",
            origine="convention",
        )
        for usage, commande in CONVENTIONS_PAR_GESTIONNAIRE.get(gestionnaire, ())
    )


def forge_depuis_distant(distant: str) -> str | None:
    """Le nom de la forge derrière une URL de remote Git, `None` si inconnue.

    Couvre les deux écritures d'un remote — `https://hôte/…` et `git@hôte:…` —
    et interroge l'hôte **deux fois** : par suffixe d'abord (la forge publique),
    puis par son premier segment (`gitlab.interne.example` → GitLab). La
    seconde lecture est un indice et n'intervient que si la première n'a rien
    donné ; sans elle, la forge d'un projet d'entreprise serait toujours
    inconnue.
    """
    correspondance = _HOTE_GIT.match(distant.strip())
    if correspondance is None:
        return None
    hote = correspondance.group(1).lower()
    for suffixe, nom in FORGE_PAR_HOTE:
        if hote == suffixe or hote.endswith(f".{suffixe}"):
            return nom
    return FORGE_PAR_SEGMENT.get(hote.split(".", 1)[0])


def usage_de_script(nom: str) -> str:
    """L'usage que le **nom** d'un script laisse deviner, `""` si aucun.

    Le nom sans extension, découpé sur `-` et `_` : `run-tests.sh` et
    `test_all.ps1` disent tous les deux « tester ». On ne lit pas le contenu du
    script : ce serait interpréter du code du projet pour un gain nul — le nom
    sert seulement à ranger, et ce qui n'est pas reconnu reste dans la liste.
    """
    morceaux = re.split(r"[-_.\s]+", Path(nom).stem.lower())
    for morceau in morceaux:
        if morceau in USAGE_PAR_NOM_DE_SCRIPT:
            return USAGE_PAR_NOM_DE_SCRIPT[morceau]
    return ""


def piece_de_convention(nom: str, chemin_relatif: str) -> Piece | None:
    """La convention que porte le fichier `nom`, `None` si ce n'en est pas une."""
    canonique = CONVENTIONS_ALIAS.get(nom, nom)
    role = CONVENTIONS_CONNUES.get(canonique)
    if role is None:
        return None
    return Piece(nom=canonique, chemin=chemin_relatif, role=role)


def _json_ou_vide(texte: str) -> dict[str, Any]:
    """Le JSON de `texte`, ou `{}` s'il ne se relit pas (tronqué, invalide)."""
    try:
        donnees = json.loads(texte)
    except (json.JSONDecodeError, ValueError):
        return {}
    return donnees if isinstance(donnees, dict) else {}


def _section_toml(donnees: dict[str, Any], chemin: str) -> dict[str, Any] | None:
    """La table TOML `a.b.c` de `donnees`, `None` si elle n'y est pas."""
    courant: Any = donnees
    for segment in chemin.split("."):
        if not isinstance(courant, dict) or segment not in courant:
            return None
        courant = courant[segment]
    return courant if isinstance(courant, dict) else None
