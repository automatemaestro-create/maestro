"""Assemble le **produit** que le paquet d'installation embarque (#641, lot de #637).

    .venv/Scripts/python.exe scripts/distribution/assembler.py \
        --sortie <dossier> --front <standalone> --node <node.exe> --cache <dossier>

Joué par `scripts/distribution/construire.sh`, qui construit le front avant et le paquet
après : ce module ne fait que poser, sous `--sortie`, un dossier **qui se suffit** —
celui que le paquet range sous `resources/produit/` et que la coque installée sert par
le lanceur du produit (`python -m maestro.lanceur`, #640) :

    produit/
      maestro/  core/  agents/  packages/shared/schemas/  docs/*.md   le produit, tel que suivi
      web/                    le front, sortie autonome de Next (`server.js`)
      runtime/python/         le CPython embarquable officiel, ses dépendances sous Lib/
      runtime/node.exe        le Node du dépôt, qui sert le front
      .env                    les réglages de la distribution bureau

## Un runtime embarqué, et non un gel

Le ticket nommait PyInstaller « ou équivalent ». L'équivalent retenu est le **paquet
embarquable officiel de CPython** (« Windows embeddable package », python.org), avec les
dépendances installées à côté de lui. Trois raisons, et la première décide :

1. **Rien n'est gelé, donc rien ne casse au gel.** Le produit se relance lui-même par
   `sys.executable -m …` (le lanceur démarre l'API ainsi, l'hôte détaché ses runs) et
   trouve le CLI `claude` **dans le paquet du SDK** (`claude_agent_sdk/_bundled/`). Un
   exécutable gelé change le sens de `sys.executable` et cache les fichiers de données
   des paquets : chacun de ces chemins serait à réécrire, et le premier oublié casserait
   chez l'utilisateur, pas ici. Un interpréteur ordinaire les garde tous.
2. **Le `._pth` isole.** Sa présence met l'interpréteur en mode isolé : il ignore
   `PYTHONPATH`, `PYTHONHOME` et le registre. Un Python installé sur le poste ne peut
   donc rien prêter à l'installation, même par accident — c'est ce qui rend la
   vérification « sans Python » honnête.
3. **Le paquet officiel est signé et publié avec son empreinte** : celle-ci est
   épinglée ici, et vérifiée à chaque assemblage.

## Ce que l'assemblage refuse

- un interpréteur de construction d'une autre version mineure que le runtime embarqué :
  les extensions compilées (`.pyd`) ne se chargent que dans la leur ;
- une empreinte qui ne correspond pas à celle que python.org publie ;
- un fichier suivi qui manque à la copie de travail ;
- un front qui porterait le **jeton de l'API du poste qui construit** : il est propre à
  chaque poste (#638) et arrive au front à son démarrage (`apps/web/lib/configPoste.ts`).
  Le trouver dans le paquet voudrait dire qu'on publie un secret avec lui.

Les codes : `0` assemblé · `1` refus nommé · `2` usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

RACINE_DEPOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Embarquable:
    """Le CPython embarquable officiel : sa version, d'où il vient, ce qu'il doit peser."""

    version: str
    url: str
    #: L'empreinte que python.org publie sur la page de la version — relevée à la main
    #: et confrontée au fichier téléchargé avant d'être écrite ici.
    sha256: str

    @property
    def mineure(self) -> tuple[int, int]:
        majeure, mineure, *_ = self.version.split(".")
        return int(majeure), int(mineure)

    @property
    def nom_pth(self) -> str:
        """Le `._pth` que l'interpréteur cherche à côté de sa DLL (`python313._pth`)."""
        majeure, mineure = self.mineure
        return f"python{majeure}{mineure}._pth"

    @property
    def nom_stdlib(self) -> str:
        majeure, mineure = self.mineure
        return f"python{majeure}{mineure}.zip"


#: Le runtime de la distribution : la version du venv de référence du dépôt (3.13.14 au
#: 2026-11-04), pour que les dépendances qu'on y pose soient celles qu'on y a testées.
PYTHON_EMBARQUE = Embarquable(
    version="3.13.14",
    url="https://www.python.org/ftp/python/3.13.14/python-3.13.14-embed-amd64.zip",
    sha256="90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907",
)

#: Ce que le produit lit **à l'exécution** sous sa racine, avec qui le lit. Une entrée de
#: moins et l'installation démarre, puis tombe le jour où ce chemin est demandé — d'où la
#: confrontation de `tests/test_distribution.py`, qui cherche dans `maestro/` chaque racine
#: que le code compose depuis son propre emplacement.
CONTENU_DU_PRODUIT: tuple[tuple[str, str], ...] = (
    ("maestro", "le moteur, l'API et le lanceur"),
    ("core", "la configuration des agents (`maestro/agents/*`, `maestro/projets/*`)"),
    ("agents", "les fiches des agents"),
    ("packages/shared/schemas", "les schémas du plan (`maestro/orchestrator/schema.py`)"),
    ("docs/*.md", "le corpus de l'assistant (`maestro/controltower/documentation.py`)"),
    ("apps/web/README.md", "le corpus de l'assistant (`MOTIFS_DOCUMENTATION`)"),
)

#: Les réglages de la distribution bureau, écrits en `.env` à la racine du produit — le
#: fichier que `maestro.config` charge sans écraser l'environnement. Le mode **local**
#: (SQLite, aucun service à installer, #639) est le défaut d'un produit posé sur un poste
#: (docs/24 §4.7) : la distribution change les défauts, pas les fonctions.
#:
#: `MAESTRO_INSTALLATION` dit au produit qu'il est installé (#1620,
#: `maestro.emplacements.installation`) : il écrit alors sous l'état du poste, jamais
#: dans ce dossier qu'une mise à jour efface. Le dire ici, plutôt que de le deviner d'un
#: chemin ou de la coque, garde le même produit derrière la fenêtre et derrière un shell.
REGLAGES_DISTRIBUTION = (
    "# Réglages de la distribution bureau de Maestro (#641) — écrits par l'installeur.\n"
    "# Une variable d'environnement du même nom passe devant. Ce fichier est remplacé à\n"
    "# chaque installation : un réglage à garder va dans ~/.maestro/configuration/.env.\n"
    "#\n"
    "# Mode local : le journal durable est un fichier SQLite sous ~/.maestro/, et\n"
    "# aucun service (Redis) n'est à installer (#639).\n"
    "MAESTRO_PERSISTANCE=sqlite\n"
    "#\n"
    "# Installé : ce que Maestro écrit va sous ~/.maestro/, jamais dans ce dossier (#1620).\n"
    "MAESTRO_INSTALLATION=1\n"
)


#: Où le produit vit sous le dossier d'installation (`extraResources` de
#: `apps/desktop/package.json`) : c'est depuis ce dossier que les chemins se mesurent.
SOUS_L_INSTALLATION = "resources/produit/"

#: La longueur maximale d'un chemin du paquet, comptée depuis le dossier d'installation.
#:
#: L'installeur NSIS extrait le paquet sous `%TEMP%\nsXXXX.tmp\7z-out\` avant de le recopier,
#: et ni lui ni l'interpréteur ne passent la limite de 260 caractères de Windows (MAX_PATH)
#: sans le réglage système `LongPathsEnabled`, qu'une installation par utilisateur ne peut
#: pas exiger. Un fichier trop profond n'est **pas refusé** : il est perdu en silence, et
#: l'installeur rend `0` quand même (constaté le 2026-11-04 : 3 665 fichiers manquants,
#: l'API morte au premier import). D'où une garde ici, avant de construire.
#:
#: Le budget : 260 − `C:\Users\` (9) − un nom de compte de 20 caractères (la limite de
#: Windows pour un compte local) − `\AppData\Local\Temp\nsXXXX.tmp\7z-out\` (38) − le zéro
#: final (1) = 192.
LONGUEUR_MAX = 192


class Refus(Exception):
    """Une raison de ne pas assembler, dite en une phrase."""



# ------------------------------------------------------------------ le runtime Python


def contenu_pth(embarquable: Embarquable) -> str:
    """Le `._pth` du runtime : ce qu'il importe, et rien d'autre.

    Chemins relatifs au dossier du `._pth` : la bibliothèque standard zippée, le dossier
    lui-même (les `.pyd` du runtime), les dépendances, puis **la racine du produit**
    (`..\\..`), où vit le paquet `maestro` à côté de `core/` — ses dépôts se résolvent par
    `Path(__file__).parents[2] / "core"`, donc il doit y rester. `import site` traite les
    `.pth` des dépendances (sans lui, celles qui en posent ne se chargent pas).
    """
    return "\r\n".join(
        (
            embarquable.nom_stdlib,
            ".",
            "Lib\\site-packages",
            "..\\..",
            "import site",
            "",
        )
    )


def empreinte(fichier: Path) -> str:
    condensat = hashlib.sha256()
    with fichier.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1 << 20), b""):
            condensat.update(bloc)
    return condensat.hexdigest()


def runtime_verifie(embarquable: Embarquable, cache: Path) -> Path:
    """L'archive embarquable, téléchargée au besoin, **toujours** confrontée à son empreinte."""
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / embarquable.url.rsplit("/", 1)[-1]
    if not archive.is_file():
        print(f"[python] téléchargement de {embarquable.url}")
        partiel = archive.with_suffix(".partiel")
        with urllib.request.urlopen(embarquable.url, timeout=120) as reponse:  # noqa: S310
            partiel.write_bytes(reponse.read())
        partiel.replace(archive)
    obtenue = empreinte(archive)
    if obtenue != embarquable.sha256:
        raise Refus(
            f"empreinte de {archive.name} inattendue : {obtenue} "
            f"(python.org publie {embarquable.sha256})"
        )
    return archive


def poser_runtime(embarquable: Embarquable, archive: Path, dossier: Path) -> None:
    with zipfile.ZipFile(archive) as zippe:
        zippe.extractall(dossier)
    (dossier / embarquable.nom_pth).write_text(contenu_pth(embarquable), encoding="utf-8")


def dependances_declarees(pyproject: Path) -> list[str]:
    """Les dépendances d'exécution du produit, telles que `pyproject.toml` les déclare."""
    projet = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    return list(projet["dependencies"])


def contraintes_du_venv(distributions: Iterable[metadata.Distribution]) -> list[str]:
    """`nom==version` pour chaque distribution installée, sauf le produit lui-même.

    Le venv de référence est celui où la suite passe : en faire les contraintes de
    l'installation, c'est poser chez l'utilisateur les versions qu'on a testées, et non
    les dernières que les bornes de `pyproject.toml` autorisent ce jour-là.
    """
    vues: dict[str, str] = {}
    for distribution in distributions:
        nom = distribution.metadata["Name"]
        if not nom or nom.lower() == "maestro":
            continue
        vues.setdefault(nom.lower(), f"{nom}=={distribution.version}")
    return sorted(vues.values())


def installer_dependances(sortie_site: Path, depot: Path, atelier: Path) -> None:
    contraintes = atelier / "contraintes.txt"
    contraintes.write_text(
        "\n".join(contraintes_du_venv(metadata.distributions())) + "\n", encoding="utf-8"
    )
    argv = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--no-warn-script-location",
        "--target",
        str(sortie_site),
        "--constraint",
        str(contraintes),
        *dependances_declarees(depot / "pyproject.toml"),
    ]
    print(f"[python] dépendances → {sortie_site}")
    subprocess.run(argv, check=True)  # noqa: S603 - argv construit ici, aucun shell
    # `--target` pose les commandes des dépendances sous `bin/` avec, en shebang, le chemin
    # absolu de l'interpréteur qui construit : un chemin de ce poste, qui ne vaut rien chez
    # l'utilisateur. Les commandes du produit, elles, sont reposées à côté de l'interpréteur
    # embarqué (`poser_lanceurs_console`).
    for dossier in ("bin", "Scripts"):
        shutil.rmtree(sortie_site / dossier, ignore_errors=True)


def points_d_entree(pyproject: Path) -> dict[str, str]:
    """Les commandes que le produit déclare (`[project.scripts]`) : nom → `module:fonction`."""
    projet = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    return dict(projet.get("scripts", {}))


def poser_lanceurs_console(dossier_python: Path, depot: Path) -> list[str]:
    """Un `.exe` par commande déclarée, **à côté de l'interpréteur** embarqué.

    C'est là que le produit les cherche (`Path(sys.executable).with_name(...)` :
    `maestro-confinement`, sans lequel une session d'agent tourne non confinée, #1279) et
    là qu'une installation `pip` les aurait posés. Le lanceur est celui de pip (distlib),
    avec un shebang **relatif** — `<launcher_dir>` désigne son propre dossier : le
    produit se déplace avec son installation, sans chemin du poste qui l'a construit.
    """
    from pip._vendor.distlib.scripts import ScriptMaker

    fabrique = ScriptMaker(None, str(dossier_python), add_launchers=True)
    fabrique.executable = "<launcher_dir>\\python.exe"
    fabrique.variants = {""}
    poses: list[str] = []
    for nom, cible in sorted(points_d_entree(depot / "pyproject.toml").items()):
        fabrique.make(f"{nom} = {cible}")
        poses.append(nom)
    return poses


# ------------------------------------------------------------------ le produit


def poser_node(node: Path, produit: Path) -> Path:
    """Le Node à embarquer, posé sous `runtime/` avec son nom (`node.exe`).

    C'est là que le lanceur cherche le Node d'une installation avant celui du `PATH`
    (`maestro/lanceur/emplacement.py`, `NODE_EMBARQUE`).
    """
    cible = produit / "runtime" / node.name
    cible.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(node, cible)
    return cible


def fichiers_suivis(depot: Path, motifs: Sequence[str]) -> list[str]:
    """Les fichiers **suivis** sous ces motifs — jamais une donnée locale du poste qui construit.

    `core/` porte, à côté de ses gabarits suivis, les dépôts que le produit remplit à
    l'usage (déclarations de projets, coffres de secrets, réglages) : non suivis, ils
    restent sur ce poste. Copier le dossier entier les publierait avec le paquet.
    """
    rendu = subprocess.run(  # noqa: S603 - argv fixe, aucun shell
        ["git", "ls-files", "-z", "--", *motifs],
        cwd=depot,
        capture_output=True,
        check=True,
    )
    return [chemin for chemin in rendu.stdout.decode("utf-8").split("\0") if chemin]


def copier_le_produit(depot: Path, produit: Path) -> int:
    motifs = [motif for motif, _ in CONTENU_DU_PRODUIT]
    suivis = fichiers_suivis(depot, motifs)
    manquants = [chemin for chemin in suivis if not (depot / chemin).is_file()]
    if manquants:
        raise Refus(f"fichiers suivis absents de la copie de travail : {', '.join(manquants[:5])}")
    for chemin in suivis:
        cible = produit / chemin
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(depot / chemin, cible)
    return len(suivis)


def copier_le_front(autonome: Path, depot: Path, produit: Path) -> None:
    """La sortie autonome de Next sous `web/`, avec ce qu'elle ne copie pas d'elle-même.

    `server.js` doit être à la **racine** de `web/` : c'est la forme que le lanceur sert
    (`maestro/lanceur/emplacement.py`), et celle qu'un front tracé depuis son propre
    dossier produit. Les fichiers statiques et `public/` sont à recopier à côté (la
    documentation de Next le dit : la sortie autonome les laisse à un CDN).
    """
    if not (autonome / "server.js").is_file():
        raise Refus(
            f"sortie autonome sans server.js à sa racine ({autonome}) — "
            "le front a-t-il été construit avec MAESTRO_FRONT_AUTONOME=1 ?"
        )
    web = produit / "web"
    shutil.copytree(autonome, web)
    front = depot / "apps" / "web"
    shutil.copytree(front / ".next" / "static", web / ".next" / "static")
    if (front / "public").is_dir():
        shutil.copytree(front / "public", web / "public")


def jeton_du_poste() -> str | None:
    """Le jeton de l'API de **ce** poste, s'il en a un — lu, jamais engendré."""
    sys.path.insert(0, str(RACINE_DEPOT))
    try:
        from maestro.controltower.acces import chemin_du_jeton
    except Exception:  # noqa: BLE001 - sans le produit importable, rien à confronter
        return None
    finally:
        sys.path.pop(0)
    try:
        return chemin_du_jeton().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def fuites_du_jeton(dossier: Path, jeton: str) -> list[Path]:
    """Les fichiers sous `dossier` qui contiennent ce jeton, octet pour octet."""
    aiguille = jeton.encode("utf-8")
    return [
        fichier
        for fichier in sorted(dossier.rglob("*"))
        if fichier.is_file() and aiguille in fichier.read_bytes()
    ]


def chemins_trop_longs(produit: Path, longueur_max: int = LONGUEUR_MAX) -> list[str]:
    """Les chemins du produit qui dépassent `longueur_max` vus du dossier d'installation."""
    trop_longs = []
    for fichier in sorted(produit.rglob("*")):
        chemin = SOUS_L_INSTALLATION + fichier.relative_to(produit).as_posix()
        if len(chemin) > longueur_max:
            trop_longs.append(chemin)
    return trop_longs


def poids(dossier: Path) -> int:
    return sum(f.stat().st_size for f in dossier.rglob("*") if f.is_file())


def assembler(
    *,
    depot: Path,
    sortie: Path,
    autonome: Path,
    node: Path,
    cache: Path,
    embarquable: Embarquable = PYTHON_EMBARQUE,
) -> dict[str, int]:
    """Pose le produit sous `sortie`, et rend le poids de chacune de ses parties."""
    if sys.version_info[:2] != embarquable.mineure:
        raise Refus(
            f"l'interpréteur qui construit est un Python {sys.version_info[0]}."
            f"{sys.version_info[1]}, le runtime embarqué un {embarquable.version} : les "
            "extensions compilées des dépendances ne se chargeraient pas"
        )
    if not node.is_file():
        raise Refus(f"Node introuvable : {node}")
    if sortie.exists():
        shutil.rmtree(sortie)
    sortie.mkdir(parents=True)
    atelier = sortie.parent / "atelier"
    atelier.mkdir(parents=True, exist_ok=True)

    python = sortie / "runtime" / "python"
    poser_runtime(embarquable, runtime_verifie(embarquable, cache), python)
    installer_dependances(python / "Lib" / "site-packages", depot, atelier)
    lanceurs = poser_lanceurs_console(python, depot)
    print(f"[python] {len(lanceurs)} commandes posées à côté de l'interpréteur")

    node_embarque = poser_node(node, sortie)

    nombre = copier_le_produit(depot, sortie)
    print(f"[produit] {nombre} fichiers suivis copiés")
    copier_le_front(autonome, depot, sortie)
    (sortie / ".env").write_text(REGLAGES_DISTRIBUTION, encoding="utf-8")

    trop_longs = chemins_trop_longs(sortie)
    if trop_longs:
        plus_long = max(trop_longs, key=len)
        raise Refus(
            f"{len(trop_longs)} chemin(s) de plus de {LONGUEUR_MAX} caractères depuis le dossier "
            f"d'installation — l'installeur les perdrait en silence ; le plus long "
            f"({len(plus_long)}) : {plus_long}"
        )

    jeton = jeton_du_poste()
    if jeton:
        fuites = fuites_du_jeton(sortie / "web", jeton)
        if fuites:
            raise Refus(
                "le jeton de l'API de ce poste est dans le front embarqué "
                f"({fuites[0].relative_to(sortie)}) — il serait publié avec le paquet"
            )

    return poids_des_parties(sortie, node_embarque)


def poids_des_parties(produit: Path, node_embarque: Path) -> dict[str, int]:
    """Le poids du produit, partie par partie — chaque octet compté une fois et une seule.

    C'est ce relevé que docs/54 §3.1 écrit : une partie oubliée ou comptée deux fois y
    fausserait la conclusion sur ce qui pèse.
    """
    python = produit / "runtime" / "python"
    return {
        "runtime/python (interpréteur et bibliothèque standard)": poids(python)
        - poids(python / "Lib"),
        "runtime/python/Lib (dépendances, CLI claude compris)": poids(python / "Lib"),
        "runtime/node.exe": node_embarque.stat().st_size,
        "web (front autonome)": poids(produit / "web"),
        "maestro, core, agents, docs": poids(produit)
        - poids(produit / "runtime")
        - poids(produit / "web"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    lecteur = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    lecteur.add_argument("--sortie", type=Path, required=True)
    lecteur.add_argument("--front", type=Path, required=True, help="sortie autonome de Next")
    lecteur.add_argument("--node", type=Path, required=True, help="l'exécutable Node à embarquer")
    lecteur.add_argument("--cache", type=Path, required=True, help="où garder l'archive Python")
    lecteur.add_argument("--rapport", type=Path, help="où écrire le poids des parties (JSON)")
    arguments = lecteur.parse_args(argv)
    try:
        parties = assembler(
            depot=RACINE_DEPOT,
            sortie=arguments.sortie,
            autonome=arguments.front,
            node=arguments.node,
            cache=arguments.cache,
        )
    except Refus as refus:
        print(f"[assemblage] refusé : {refus}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as erreur:
        print(
            f"[assemblage] commande en échec ({erreur.returncode}) : {erreur.cmd}",
            file=sys.stderr,
        )
        return 1
    for partie, octets in parties.items():
        print(f"  {octets / 1e6:8.1f} Mo  {partie}")
    if arguments.rapport is not None:
        arguments.rapport.write_text(
            json.dumps(parties, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    # Redirigée vers un journal, la sortie de Python sous Windows prend la page de code du
    # poste (cp1252), qui ne sait pas écrire tout ce que ce module dit.
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
