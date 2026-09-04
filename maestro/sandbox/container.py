"""Mode isolé — l'exécution outillée dans un conteneur Docker durci (ticket #108).

Renforce l'isolation d'exécution des agents au-delà du répertoire jetable
(`maestro.sandbox.workspace`) : en mode isolé (**opt-in**, `MAESTRO_ISOLATION=conteneur`),
le CLI que pilote l'Agent SDK — et tout ce qu'il lance : outils, Bash, serveurs MCP
stdio, code produit — tourne dans un **conteneur Docker durci** jetable, un par
exécution outillée, au lieu de s'exécuter avec les droits du process hôte.

La couture est le champ `cli_path` de l'Agent SDK : au lieu de lancer le CLI
directement, le fournisseur pointe le SDK sur le shim `maestro-sandbox-shim`
(`maestro.sandbox.shim`), qui relaie la commande — et son flux stdio — vers
`docker run` (`commande_docker`). Le protocole entre les deux est un petit jeu de
variables d'environnement `MAESTRO_SANDBOX_*` (`IsolationConfig.env_sandbox`),
posées par le fournisseur sur le sous-processus via `ClaudeAgentOptions.env`.

Ce qui reste sur l'hôte : le moteur, le SDK, la télémétrie, le journal et les
garde-fous — seul le sous-processus CLI change de monde. Les **accès accordés**
au conteneur sont volontairement énumérés ici (montage du seul workspace,
variables d'auth `ENV_TRANSMISES`, réseau sortant) et documentés dans
docs/17-isolation-execution.md.

**Le projet de l'utilisateur (#226, Phase 7)** déplace cette frontière, et c'est
le seul endroit du contrat qui bouge. Jusqu'ici le workspace monté était un
répertoire jetable créé vide : le conteneur ne touchait *aucun* chemin de
l'hôte porteur de données. Depuis #224, une tâche rattachée à un projet
travaille dans un espace **dérivé** de ce projet — worktree Git sur une branche
`maestro/<tâche>`, ou copie du périmètre — et c'est lui qui est monté. Trois
conséquences, portées ici :

- l'espace dérivé **est** l'espace de travail de la tâche : le « second montage »
  annoncé par docs/17 §3 se matérialise **à la place** du répertoire jetable sur
  `/workspace`, il ne s'y ajoute pas un troisième chemin (ce serait monter deux
  fois le même répertoire) ;
- la **racine d'un projet versionné n'est jamais montée** — ni ici ni ailleurs.
  C'est vérifié plutôt que supposé, et deux fois : au câblage du protocole
  (`env_sandbox`) et au dernier mètre avant `docker run` (`commande_docker`),
  qui est la seule porte que rien ne contourne. ⚠ Depuis #839 la restriction
  vaut pour le projet **versionné** : un projet non versionné travaille **dans
  sa racine** (`maestro.sandbox.en_place`), qui est donc l'espace monté —
  avec ses masques, ce qui fait du conteneur l'endroit où ses exclusions
  deviennent une clôture dure ;
- les **exclusions du périmètre** (`.env`, `**/secrets/**`…) valent jusque dans
  le conteneur : ce qui est exclu n'y est **pas monté**, chaque chemin exclu
  étant recouvert d'un montage vide en lecture seule (`_masques`). Le cas n'est
  pas théorique — le **worktree** d'un projet versionné est une copie conforme
  de la branche, un `.env` ou un `secrets/` **versionnés** y sont bel et bien,
  et la racine d'un projet non versionné porte tout ce que l'utilisateur y a mis.

La rédaction des secrets du projet, elle, ne dépend pas du mode isolé (une tâche
sur l'hôte fuit tout autant) : elle vit dans `maestro.projets.secrets` et est
armée par `maestro.agents.runtime`.

Choix d'implémentation (notes du ticket) : conteneur durci plutôt que micro-VM —
sur le poste de développement Windows, Docker Desktop exécute déjà les conteneurs
dans une VM utilitaire (WSL2) ; gVisor/Firecracker, Linux seulement, restent la
piste « serveur » (réévaluée avec #107/#102).
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.config import ConfigError, Settings
from maestro.projets.modele import Projet
from maestro.projets.perimetre import Exclu, exclusions
from maestro.projets.racine import canonique

#: Valeur de `MAESTRO_ISOLATION` qui active le mode isolé. Toute autre valeur non
#: vide est une erreur de config explicite (pas d'isolation « silencieusement absente »).
MODE_CONTENEUR = "conteneur"

#: Image par défaut du conteneur d'exécution — construite depuis `infra/sandbox/` :
#: `docker build -t maestro-sandbox:latest infra/sandbox`.
IMAGE_DEFAUT = "maestro-sandbox:latest"

#: Réseaux Docker acceptés : `bridge` (sortant seul, aucun port publié — défaut,
#: le CLI doit joindre l'API du fournisseur) ou `none` (aucun réseau — diagnostic
#: seulement, l'API modèle est alors injoignable).
RESEAUX_VALIDES: tuple[str, ...] = ("bridge", "none")
RESEAU_DEFAUT = "bridge"

#: Protocole fournisseur → shim : variables posées sur le sous-processus CLI
#: (via `ClaudeAgentOptions.env`), lues par `maestro.sandbox.shim`.
ENV_IMAGE = "MAESTRO_SANDBOX_IMAGE"
ENV_RESEAU = "MAESTRO_SANDBOX_RESEAU"
ENV_WORKSPACE = "MAESTRO_SANDBOX_WORKSPACE"

#: Racine du projet de la tâche (#226), transmise **pour être refusée** : elle
#: n'est jamais montée, et sa présence permet au shim de le vérifier au dernier
#: mètre plutôt que de faire confiance à l'appelant. Absente : tâche sans projet.
ENV_PROJET = "MAESTRO_SANDBOX_PROJET"

#: Chemins du périmètre exclus, présents dans l'espace de travail et masqués dans
#: le conteneur (#226). Une entrée par ligne, préfixée de sa nature — `d:` pour un
#: dossier, `f:` pour un fichier —, le chemin étant relatif POSIX à l'espace :
#: `f:.env`, `d:services/api/secrets`. Le saut de ligne sépare parce qu'il est le
#: seul caractère qu'un nom de fichier ne porte pas en pratique, là où `;` et `:`
#: sont l'un légal et l'autre partout dans les chemins Windows.
ENV_MASQUES = "MAESTRO_SANDBOX_MASQUES"

#: Point de montage de l'espace de travail dans le conteneur — préfixe des masques.
POINT_MONTAGE = "/workspace"

#: Plafond de masques. Au-delà, on **refuse** l'exécution au lieu de monter à
#: moitié : dépasser 256 chemins exclus distincts signale un périmètre mal écrit
#: (des motifs par fichier au lieu d'un motif par dossier), et un secret monté
#: par débordement de liste serait exactement l'accident que ce lot ferme.
_MASQUES_MAX = 256

#: Séparateur des entrées de `ENV_MASQUES`.
_SEPARATEUR_MASQUES = "\n"

#: Répertoire des masques du processus courant (cf. `_vides`) — None tant qu'aucun
#: périmètre n'a eu de chemin à masquer.
_RACINE_VIDES: Path | None = None

#: Variables d'authentification transmises au conteneur — la SEULE part de
#: l'environnement hôte qui y entre. Les chaînes vides sont transmises telles
#: quelles : c'est la neutralisation des credentials concurrents posée par le
#: fournisseur (`maestro.providers.claude._auth_env`), à préserver dans le
#: conteneur. Les secrets MCP (#104) n'en font pas partie : déjà résolus en
#: mémoire, ils voyagent dans la config MCP portée par les arguments du CLI.
ENV_TRANSMISES: tuple[str, ...] = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)

#: uid/gid de l'utilisateur non-root `agent` de l'image dédiée (infra/sandbox/
#: Dockerfile) — repris pour le tmpfs monté sur son home.
_UID_AGENT = 10001

#: Plafonds de ressources du conteneur (anti-emballement : un agent qui compile
#: ou boucle ne met pas le poste à genoux). Fixes au POC — à rendre configurables
#: si un rôle légitime les crève.
_PIDS_MAX = 256
_MEMOIRE_MAX = "2g"
_CPUS_MAX = "2"

#: Nom du shim (point d'entrée console déclaré dans pyproject.toml).
_NOM_SHIM = "maestro-sandbox-shim"


@dataclass(frozen=True)
class IsolationConfig:
    """Réglages du mode isolé, dérivés de la config — None = exécution sur l'hôte.

    `shim` est le chemin résolu de l'exécutable `maestro-sandbox-shim` : c'est la
    valeur que le fournisseur passe en `cli_path` à l'Agent SDK.
    """

    image: str
    reseau: str
    shim: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> IsolationConfig | None:
        """Interprète `MAESTRO_ISOLATION*` : None si le mode isolé n'est pas demandé.

        Valide tout ce qui peut l'être **au câblage** (valeur du mode, réseau,
        présence du shim) : une config d'isolation bancale casse au démarrage,
        pas au milieu d'une exécution. La présence de Docker et de l'image,
        elles, ne se constatent qu'au lancement — leur absence remonte en échec
        de tâche, consigné comme les autres.
        """
        mode = settings.isolation
        if mode is None:
            return None
        if mode != MODE_CONTENEUR:
            raise ConfigError(
                f"MAESTRO_ISOLATION={mode!r} inconnu. Valeurs acceptées : vide "
                f"(exécution sur l'hôte, défaut) ou {MODE_CONTENEUR!r}."
            )
        reseau = settings.isolation_reseau or RESEAU_DEFAUT
        if reseau not in RESEAUX_VALIDES:
            valides = ", ".join(RESEAUX_VALIDES)
            raise ConfigError(
                f"MAESTRO_ISOLATION_RESEAU={reseau!r} inconnu. Valeurs acceptées : {valides}."
            )
        return cls(
            image=settings.isolation_image or IMAGE_DEFAUT,
            reseau=reseau,
            shim=_chemin_shim(),
        )

    def env_sandbox(self, workspace: Path, *, projet: Projet | None = None) -> dict[str, str]:
        """Les variables `MAESTRO_SANDBOX_*` à poser sur le sous-processus shim.

        `workspace` est l'espace de travail de la tâche (`maestro.sandbox`) : le
        seul chemin de l'hôte que le shim montera dans le conteneur.

        `projet` (#226) est le projet dans lequel la tâche travaille — auquel cas
        `workspace` est l'espace **dérivé** de ce projet (#224). S'y ajoute alors
        la liste des chemins exclus par le périmètre (`ENV_MASQUES`), que le shim
        recouvrira de montages vides. None — une tâche sans `projet_id` — rend
        exactement les trois variables d'avant.

        Le sort de la **racine** dépend du régime, et c'est le seul point que
        #839 a déplacé. Projet **versionné** : l'espace est un worktree hors de
        la racine, celle-ci est transmise (`ENV_PROJET`) **pour être refusée** au
        dernier mètre, et un espace qui serait la racine ou vivrait dedans est
        refusé ici même (EF-36). Projet **non versionné** : l'espace **est** la
        racine — le régime en place (`maestro.sandbox.en_place`) —, elle est donc
        montée telle quelle, **avec ses masques** : c'est ici que les exclusions
        deviennent une clôture dure, là où sur l'hôte la frontière d'écriture ne
        confronte que les outils de fichiers. `ENV_PROJET` n'est pas transmise :
        il n'y a rien à refuser, et la transmettre ferait refuser le montage
        même qu'on vient de décider.

        Lève `ConfigError` si le périmètre exclut plus de chemins que
        `_MASQUES_MAX` — un refus franc plutôt qu'un montage approximatif.
        """
        protocole = {
            ENV_IMAGE: self.image,
            ENV_RESEAU: self.reseau,
            ENV_WORKSPACE: str(workspace),
        }
        if projet is None:
            return protocole
        racine = canonique(projet.racine_chemin)
        if projet.versionne:
            _refuse_la_racine(workspace, racine)
            protocole[ENV_PROJET] = str(racine)
        masques = exclusions(workspace, projet.perimetre)
        if len(masques) > _MASQUES_MAX:
            raise ConfigError(
                f"Périmètre du projet {projet.id} : {len(masques)} chemins exclus "
                f"présents dans l'espace de travail, au-delà des {_MASQUES_MAX} que "
                "le conteneur sait masquer. Élargissez les motifs (un dossier plutôt "
                "que ses fichiers) — l'exécution est refusée plutôt que de monter "
                "une partie de ce qui est exclu."
            )
        return protocole | {
            ENV_MASQUES: _SEPARATEUR_MASQUES.join(_encode(masque) for masque in masques),
        }


def commande_docker(environ: Mapping[str, str], arguments: Sequence[str]) -> list[str]:
    """Construit la commande `docker run` durcie exécutant le CLI dans le conteneur.

    `environ` est l'environnement du shim (protocole `MAESTRO_SANDBOX_*` + le
    reste, hérité) ; `arguments` sont les arguments que l'Agent SDK destinait au
    CLI — relayés tels quels au `claude` de l'image. Durcissement appliqué :

    - **système de fichiers borné** : racine en lecture seule (`--read-only`),
      seuls le workspace de la tâche (monté sur `/workspace`, lecture-écriture)
      et deux tmpfs jetables (`/tmp`, home de l'utilisateur `agent` — l'état du
      CLI ne survit pas au conteneur) sont inscriptibles. Quand la tâche
      travaille dans un projet (#226), ce workspace est l'espace **dérivé** de
      ce projet — **jamais la racine d'un projet versionné** (refus explicite),
      la racine elle-même pour un projet non versionné (#839) —, et les chemins
      que son périmètre exclut sont recouverts d'un montage vide en lecture
      seule ;
    - **réseau restreint** : `bridge` (sortant seul, aucun port publié) ou `none` ;
    - **privilèges retirés** : utilisateur non-root (image), `--cap-drop ALL`,
      `--security-opt no-new-privileges`, plafonds pids/mémoire/CPU ;
    - **environnement minimal** : seules les variables d'auth `ENV_TRANSMISES`
      entrent — jamais l'environnement hôte entier.

    Lève `ConfigError` si le protocole `MAESTRO_SANDBOX_*` est absent : le shim
    ne s'invoque que via le mode isolé du fournisseur, pas à la main. Et si le
    workspace à monter est la racine du projet (ou vit dedans) : c'est le dernier
    contrôle avant `docker run`, celui que rien ne contourne.
    """
    image = (environ.get(ENV_IMAGE) or "").strip()
    reseau = (environ.get(ENV_RESEAU) or "").strip()
    workspace = (environ.get(ENV_WORKSPACE) or "").strip()
    if not image or not reseau or not workspace:
        raise ConfigError(
            f"variables {ENV_IMAGE}/{ENV_RESEAU}/{ENV_WORKSPACE} absentes ou vides — "
            "le shim est lancé par le mode isolé du fournisseur "
            "(MAESTRO_ISOLATION=conteneur), pas directement."
        )
    racine = (environ.get(ENV_PROJET) or "").strip()
    if racine:
        _refuse_la_racine(Path(workspace), Path(racine))
    commande = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--init",
        "--network",
        reseau,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,exec,size=512m",
        "--tmpfs",
        f"/home/agent:rw,exec,size=1g,uid={_UID_AGENT},gid={_UID_AGENT}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(_PIDS_MAX),
        "--memory",
        _MEMOIRE_MAX,
        "--cpus",
        _CPUS_MAX,
        "--volume",
        f"{workspace}:{POINT_MONTAGE}",
        "--workdir",
        POINT_MONTAGE,
    ]
    commande += _montages_masques(environ.get(ENV_MASQUES) or "")
    for variable in ENV_TRANSMISES:
        if variable in environ:
            commande += ["--env", f"{variable}={environ[variable]}"]
    return [*commande, image, "claude", *arguments]


def _refuse_la_racine(workspace: Path, racine: Path) -> None:
    """Refuse de monter un espace de travail qui **est** la racine ou vit dedans.

    EF-36 dans sa forme la plus courte : sur un projet **versionné**, les agents
    travaillent hors de la racine déclarée. Le lot 4 (#224) le vérifie déjà côté
    hôte au montage du worktree ; on le revérifie ici parce que le chemin
    traverse entre-temps un protocole d'environnement, qu'un tiers peut poser, et
    parce que la même règle vérifiée deux fois coûte deux comparaisons de
    chemins. Un projet non versionné ne passe pas par ici (#839) : sa racine
    **est** l'espace, et `env_sandbox` ne transmet alors pas `ENV_PROJET`.

    La comparaison se fait sur des chemins **résolus** : `racine/../racine`, un
    lien symbolique vers la racine et une casse différente sous Windows désignent
    tous le même dossier, et aucun ne doit passer.
    """
    cible = _resolu(workspace)
    base = _resolu(racine)
    if cible != base and base not in cible.parents:
        return
    raise ConfigError(
        f"Montage refusé : l'espace de travail {workspace} est la racine du projet "
        f"({racine}) ou vit dedans. Un agent travaille dans l'espace dérivé du "
        "projet — worktree ou copie —, jamais dans la racine elle-même (EF-36)."
    )


def _resolu(chemin: Path) -> Path:
    """Le chemin canonicalisé, tel quel s'il est illisible (comparaison au pire littérale)."""
    try:
        return Path(os.path.normcase(chemin.resolve()))
    except OSError:
        return Path(os.path.normcase(chemin))


def _encode(masque: Exclu) -> str:
    """Une entrée de `ENV_MASQUES` : `d:<chemin>` pour un dossier, `f:` pour un fichier."""
    return f"{'d' if masque.dossier else 'f'}:{masque.chemin}"


def _montages_masques(masques: str) -> list[str]:
    """Les `--volume` qui recouvrent d'un vide chaque chemin exclu du périmètre.

    « Ce qui est exclu n'est pas monté » : Docker n'ayant pas de motif
    d'exclusion sur un montage, on monte le vide **par-dessus** — un fichier vide
    sur un fichier, un dossier vide sur un dossier, tous deux en lecture seule.
    Le contenu de l'hôte ne traverse jamais : c'est le vide qui est monté, et le
    conteneur voit un `.env` de zéro octet là où le projet en versionnait un.

    Les montages imbriqués sont ordonnés par Docker sur la profondeur de leur
    destination : les masques s'appliquent donc bien **après** le montage de
    l'espace, quel que soit leur rang dans la commande.
    """
    entrees = [entree for entree in masques.split(_SEPARATEUR_MASQUES) if entree.strip()]
    if not entrees:
        return []
    vide_fichier, vide_dossier = _vides()
    montages: list[str] = []
    for entree in entrees:
        nature, _, chemin = entree.partition(":")
        if not chemin:
            continue
        source = vide_dossier if nature == "d" else vide_fichier
        montages += ["--volume", f"{source}:{POINT_MONTAGE}/{chemin}:ro"]
    return montages


def _vides() -> tuple[Path, Path]:
    """Le fichier vide et le dossier vide qui servent de masques, créés une fois.

    Vivent dans le répertoire temporaire du système et non sous `.maestro/` :
    personne ne les lit jamais — ce sont deux inodes sans contenu, dont l'unique
    raison d'être est d'exister le temps du `docker run` (cf. CLAUDE.md, « ce que
    personne ne lit reste dans `${TMPDIR:-/tmp}` »). Créés à la demande, partagés
    par tous les masques du processus, et retirés à sa sortie.
    """
    global _RACINE_VIDES
    if _RACINE_VIDES is None:
        _RACINE_VIDES = Path(tempfile.mkdtemp(prefix="maestro-masque-"))
        atexit.register(shutil.rmtree, _RACINE_VIDES, True)
        (_RACINE_VIDES / "vide").touch()
        (_RACINE_VIDES / "vide.d").mkdir()
    return _RACINE_VIDES / "vide", _RACINE_VIDES / "vide.d"


def _chemin_shim() -> Path:
    """Résout l'exécutable du shim, installé à côté de l'interpréteur courant.

    Cherche d'abord dans le répertoire des scripts du venv (le cas nominal :
    le paquet est installé en editable), puis sur le PATH. Introuvable = erreur
    de config — le mode isolé ne peut pas fonctionner sans lui.
    """
    nom = _NOM_SHIM + (".exe" if sys.platform == "win32" else "")
    candidat = Path(sys.executable).with_name(nom)
    if candidat.exists():
        return candidat
    trouve = shutil.which(_NOM_SHIM)
    if trouve:
        return Path(trouve)
    raise ConfigError(
        f"MAESTRO_ISOLATION={MODE_CONTENEUR} mais l'exécutable {_NOM_SHIM!r} est "
        "introuvable (répertoire des scripts du venv, puis PATH). Réinstallez le "
        "paquet : pip install -e ."
    )
