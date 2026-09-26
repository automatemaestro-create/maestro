"""Les clients d'agents installés sur ce poste, avec leur version (#1295).

La moitié « poste » de la règle du pont : un `CLAUDE.md` ou un `GEMINI.md` ne s'écrit que pour
un client que la personne utilise et qui ne lit pas `AGENTS.md` à sa version
(`maestro.outillage.clients`). Savoir qu'il est installé ne suffit donc pas — Claude Code lit
`AGENTS.md` depuis la v2.1.277, pas avant —, et c'est ce qui distingue ce module de la sonde du
poste (`maestro.poste`, #487) :

- **la sonde n'exécute rien**, pas même `--version` : ce qu'elle dit — « cet outil est là » —
  ne dépend d'aucune version, et c'est au moment d'ouvrir un formulaire qu'elle répond ;
- **ici, la version décide d'un fichier écrit dans le projet de quelqu'un.** La supposer serait
  écrire un pont qui masque la lecture native, ou n'en pas écrire un qui manque. On la **lit**,
  donc : la commande résolue sur le `PATH`, lancée avec `--version` et rien d'autre.

Ce que ce lancement s'interdit, parce que c'est le seul processus que ce module ouvre : aucun
autre argument que `--version`, l'entrée fermée, aucune console ouverte sous Windows, un délai
court — une version qui ne vient pas est **inconnue**, jamais devinée — et aucun réseau demandé.
Il vit **hors** de `maestro.outillage`, dont aucun module n'importe de quoi exécuter (la promesse
de l'analyse, gardée par `tests/test_outillage_analyse.py`).

**Un client introuvable n'est pas supposé présent** : le `PATH` du process qui sert l'API n'est
pas toujours celui du terminal de la personne (`maestro.poste.INCERTITUDE_PATH`), et c'est
pourquoi elle peut toujours le dire dans la conversation (« j'utilise aussi Gemini CLI »).

**Doublable de bout en bout** : `resoudre` et `lire_version` sont les deux portes vers le poste,
et `detecter` les prend en paramètres. `tests/conftest.py` ferme la première pour toute la suite
— un poste nu, celui de la CI — sauf au test qui la veut (`@pytest.mark.clients_du_poste`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable

from maestro.outillage.clients import CLIENTS_CONNUS, SOURCE_POSTE, Client, version_de

#: Ce qu'on attend d'un `--version`, au plus. Un CLI Node démarre en une seconde ; au-delà, la
#: version est dite inconnue plutôt que d'allonger l'analyse qui l'attend.
DELAI_VERSION_S = 10.0

#: Résout une commande en chemin, ou `None` (la signature de `shutil.which`).
Resolveur = Callable[[str], str | None]

#: Lit la version du binaire à ce chemin, ou `None`.
Versionneur = Callable[[str], str | None]


def resoudre(commande: str) -> str | None:
    """Le chemin de `commande` sur le `PATH` de ce process — une lecture, rien de lancé."""
    return shutil.which(commande)


def lire_version(chemin: str, *, delai_s: float = DELAI_VERSION_S) -> str | None:
    """La version que rend `<chemin> --version`, `None` si elle ne vient pas.

    Toute panne — binaire qui refuse, délai dépassé, sortie sans version — rend `None` : une
    version inconnue est une réponse, et c'est la règle du pont qui décide quoi en faire.
    """
    # Aucune console ouverte sous Windows ; ailleurs, le drapeau n'existe pas et vaut 0.
    sans_console = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        sortie = subprocess.run(  # noqa: S603 — un chemin résolu, un seul argument fixe
            [chemin, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=delai_s,
            stdin=subprocess.DEVNULL,
            check=False,
            creationflags=sans_console,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if sortie.returncode != 0:
        return None
    return version_de(sortie.stdout) or version_de(sortie.stderr)


def detecter(
    *,
    resolveur: Resolveur | None = None,
    versionneur: Versionneur | None = None,
) -> tuple[Client, ...]:
    """Les clients connus installés ici, dans l'ordre de la table, avec leur version.

    La version n'est lue que pour un client à qui un pont **peut** manquer : pour les autres,
    elle ne décide de rien, et la lire serait lancer un processus pour rien.
    """
    resolveur = resolveur or resoudre
    versionneur = versionneur or lire_version
    trouves: list[Client] = []
    for connu in CLIENTS_CONNUS:
        if not connu.commande:
            continue
        chemin = resolveur(connu.commande)
        if not chemin:
            continue
        trouves.append(
            Client(
                cle=connu.cle,
                libelle=connu.libelle,
                version=versionneur(chemin) if connu.pont else None,
                source=SOURCE_POSTE,
                origine=chemin,
            )
        )
    return tuple(trouves)


__all__ = ["DELAI_VERSION_S", "detecter", "lire_version", "resoudre"]
