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

Choix d'implémentation (notes du ticket) : conteneur durci plutôt que micro-VM —
sur le poste de développement Windows, Docker Desktop exécute déjà les conteneurs
dans une VM utilitaire (WSL2) ; gVisor/Firecracker, Linux seulement, restent la
piste « serveur » (réévaluée avec #107/#102).
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.config import ConfigError, Settings

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

    def env_sandbox(self, workspace: Path) -> dict[str, str]:
        """Les variables `MAESTRO_SANDBOX_*` à poser sur le sous-processus shim.

        `workspace` est l'espace jetable de la tâche (`maestro.sandbox.workspace`) :
        le seul chemin de l'hôte que le shim montera dans le conteneur.
        """
        return {
            ENV_IMAGE: self.image,
            ENV_RESEAU: self.reseau,
            ENV_WORKSPACE: str(workspace),
        }


def commande_docker(environ: Mapping[str, str], arguments: Sequence[str]) -> list[str]:
    """Construit la commande `docker run` durcie exécutant le CLI dans le conteneur.

    `environ` est l'environnement du shim (protocole `MAESTRO_SANDBOX_*` + le
    reste, hérité) ; `arguments` sont les arguments que l'Agent SDK destinait au
    CLI — relayés tels quels au `claude` de l'image. Durcissement appliqué :

    - **système de fichiers borné** : racine en lecture seule (`--read-only`),
      seuls le workspace de la tâche (monté sur `/workspace`, lecture-écriture)
      et deux tmpfs jetables (`/tmp`, home de l'utilisateur `agent` — l'état du
      CLI ne survit pas au conteneur) sont inscriptibles ;
    - **réseau restreint** : `bridge` (sortant seul, aucun port publié) ou `none` ;
    - **privilèges retirés** : utilisateur non-root (image), `--cap-drop ALL`,
      `--security-opt no-new-privileges`, plafonds pids/mémoire/CPU ;
    - **environnement minimal** : seules les variables d'auth `ENV_TRANSMISES`
      entrent — jamais l'environnement hôte entier.

    Lève `ConfigError` si le protocole `MAESTRO_SANDBOX_*` est absent : le shim
    ne s'invoque que via le mode isolé du fournisseur, pas à la main.
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
        f"{workspace}:/workspace",
        "--workdir",
        "/workspace",
    ]
    for variable in ENV_TRANSMISES:
        if variable in environ:
            commande += ["--env", f"{variable}={environ[variable]}"]
    return [*commande, image, "claude", *arguments]


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
