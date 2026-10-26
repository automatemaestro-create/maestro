"""Le navigateur de Maestro : ce qu'une équipe qui construit une interface web reçoit (#1532).

Sur p5 (run `fea601a5c629`, docs/50 § 4.1), l'équipe — développeur Next.js, testeur,
designer — n'avait **aucun navigateur**. Pour regarder ce qu'ils construisaient, ses
agents ont écrit eux-mêmes leurs bancs : Chrome sans fenêtre piloté par CDP, iframes
à 375 px, puppeteer installé dans un établi temporaire. Huit minutes et 0,70 $ de
bancs écrits à la main, et deux arbitrages nés de fichiers posés hors du projet. Le
registre proposait pourtant un serveur navigateur, mais **sur activation** : un geste
que la personne ne sait pas faire. Maestro possède ses outils (docs/44) — ce dont un
agent a besoin pour sa tâche ne dépend pas de ce geste.

Ce module dit ce qu'est ce navigateur, et rien d'autre : **qui le reçoit** se décide
à la composition de l'équipe (`maestro.equipe.composition` — le modèle juge, la
personne corrige en mots), **ce que l'agent en sait** s'écrit dans son playbook à la
création (`maestro.equipe.creation`), et **l'écriture** dans le pool du projet est
celle de `maestro.controltower.equipe`.

## Un adaptateur, sous un nom qui est le nôtre

Le serveur est l'entrée `playwright` de l'**allowlist** (`maestro.agents.mcp_registry`) :
c'est elle qu'on instancie, jamais un gabarit écrit ici — le garde-fou supply-chain
reste celui du registre. Sa version y est **figée**, il y tourne sans fenêtre et sous
un profil tenu en mémoire (`--headless`, `--isolated`). Deux choses s'y ajoutent au
moment d'équiper, parce qu'elles ne se savent que sur le poste :

- **le navigateur qu'il pilote** (`canal_du_poste`) : Chrome s'il est installé, sinon
  Edge — tous deux des « canaux » que Playwright sait piloter sans rien télécharger.
  Aucun : le serveur garde son défaut, et c'est sa propre erreur, lisible, que
  l'agent lira à son premier geste ;
- **où vont ses captures** (`dossier_des_sorties`) : sous le jetable du poste, jamais
  dans le dossier de la tâche. Son défaut est `.playwright-mcp/` dans le répertoire
  courant — l'espace de la tâche, dont tout fichier ressort en livrable et part dans
  le projet de la personne à la fusion.

L'intégration garde l'id du registre (`ID_INTEGRATION`) : l'écran des intégrations
la reconnaît comme l'entrée curée qu'elle est. Le **serveur**, lui, s'appelle
`navigateur` (`NOM_SERVEUR`) : c'est le préfixe des outils que l'agent voit
(`mcp__navigateur__…`), et le nom de l'adaptateur n'a pas à y paraître.

## Ce qui rend son processus à la fin de la tâche

Rien ici, et c'est voulu : le serveur est un enfant de la session de l'agent, née
dans le conteneur de processus de sa tâche (`maestro.sandbox.arbre`, #1279). Tout ce
qu'il lance — le navigateur compris — est arrêté avec elle. Le profil tenu en mémoire
ne laisse rien sur le disque, et deux agents en parallèle ne se disputent pas un
profil (la limite que l'éditeur écrit lui-même : un profil persistant n'accepte qu'un
navigateur à la fois).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path, PureWindowsPath

from maestro.agents.mcp import IntegrationMcp, ServeurMcp
from maestro.agents.mcp_registry import RegistreMcp
from maestro.emplacements import racine_jetable

#: L'id de l'entrée du registre — et de l'intégration au pool du projet.
ID_INTEGRATION = "playwright"

#: Le nom du serveur monté : le préfixe des outils que l'agent voit
#: (`mcp__navigateur__browser_navigate`…).
NOM_SERVEUR = "navigateur"

#: Le nom du navigateur dans une phrase (« Tu as le navigateur de Maestro »), et
#: tel qu'il s'affiche seul, en tête d'une ligne de l'équipe proposée.
LIBELLE = "le navigateur de Maestro"
NOM = "Navigateur de Maestro"

#: Ce qu'il permet, dans l'ordre où un agent qui regarde un écran s'en sert, et
#: **dans les mots de la personne** : c'est ce que l'équipe proposée lui montre, sur
#: la face de la carte comme dans la ligne d'un rôle (variante B de #1532). L'agent
#: lit les mêmes promesses, un geste par promesse, outils compris
#: (`GESTES_DE_L_AGENT`) : deux listes qui ne tiendraient pas le même compte
#: finiraient par ne plus promettre la même chose.
CE_QU_IL_FAIT: tuple[str, ...] = (
    "ouvrir et parcourir une page",
    "la capturer à une largeur donnée",
    "émuler la réduction des animations",
    "lire les erreurs de la console",
)

#: Ce que l'agent lit dans son playbook (`maestro.equipe.creation`) : chaque geste
#: de `CE_QU_IL_FAIT`, avec les outils du serveur qui le font. C'est le seul endroit
#: qui nomme les outils de l'adaptateur — le remplacer, c'est réécrire ces lignes.
GESTES_DE_L_AGENT: tuple[str, ...] = (
    "ouvrir une page et la parcourir — `browser_navigate`, `browser_snapshot` (l'arbre "
    "d'accessibilité de la page), `browser_click`, `browser_type`, `browser_fill_form` ;",
    "la capturer à une largeur donnée — `browser_resize`, puis `browser_take_screenshot` ;",
    "émuler la réduction des animations — `browser_run_code_unsafe` avec "
    "`async (page) => { await page.emulateMedia({ reducedMotion: 'reduce' }) }`, puis "
    "recharger la page ;",
    "lire les erreurs de la console — `browser_console_messages` (niveau `error`).",
)

#: Ce qui suit les gestes : la consigne que p5 a montrée nécessaire (docs/50 § 4.1).
#:
#: ⚠ Sa dernière phrase vient de p5 **rejoué** avec le navigateur (run `6193eadac741`,
#: 2026-10-22) : le serveur range son journal de console dans un fichier de son dossier
#: de sorties, hors du projet, et y renvoie après chaque geste (« New console entries :
#: …log#L5-L11 »). Deux agents l'ont lu au shell (`sed -n`, `cat`), et chaque lecture
#: hors du projet a attendu qu'une personne tranche. L'outil `Read` le lit sans rien
#: demander, et `browser_console_messages` rend la console sans fichier.
CONSIGNE_DE_L_AGENT = (
    "N'écris pas ton propre banc pour regarder une page : ni Chrome piloté par CDP, ni "
    "puppeteer ou Playwright installés pour l'occasion, ni fichier d'essai posé hors du "
    "projet. Le navigateur est déjà là, sans fenêtre, sous un profil qui ne laisse rien "
    "derrière lui, et il s'arrête avec ta tâche. Lance le serveur du projet, ouvre son "
    "adresse locale, regarde, puis arrête ce que tu as lancé. Ce que le navigateur "
    "écrit — captures, journal de la console — tombe hors du projet : demande la "
    "console à `browser_console_messages`, lis un fichier qu'il te désigne avec l'outil "
    "`Read`, jamais au shell, où une lecture hors du projet attend qu'une personne tranche."
)

#: Le sous-dossier du jetable où tombent ses captures (voir l'en-tête).
NOM_SORTIES = "navigateur"

#: Les canaux que Playwright pilote sans rien télécharger, dans l'ordre de
#: préférence, avec les exécutables qui trahissent leur présence. Chrome d'abord :
#: c'est le canal par défaut du serveur, donc celui que ses exemples supposent.
_CANAUX: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "chrome",
        (
            ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
            ("PROGRAMFILES", r"Google\Chrome\Application\chrome.exe"),
            ("PROGRAMFILES(X86)", r"Google\Chrome\Application\chrome.exe"),
        ),
    ),
    (
        "msedge",
        (
            ("PROGRAMFILES(X86)", r"Microsoft\Edge\Application\msedge.exe"),
            ("PROGRAMFILES", r"Microsoft\Edge\Application\msedge.exe"),
            ("LOCALAPPDATA", r"Microsoft\Edge\Application\msedge.exe"),
        ),
    ),
)

#: Hors de Windows, les chemins d'installation sont fixes.
_CHEMINS_FIXES: dict[str, dict[str, tuple[str, ...]]] = {
    "darwin": {
        "chrome": ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",),
        "msedge": ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",),
    },
    "linux": {
        "chrome": ("/opt/google/chrome/chrome",),
        "msedge": ("/opt/microsoft/msedge/msedge",),
    },
}


def canal_du_poste(
    *,
    environ: Mapping[str, str] | None = None,
    plateforme: str | None = None,
    existe: Callable[[Path], bool] | None = None,
) -> str | None:
    """Le navigateur que le serveur pilotera sur ce poste — `chrome`, `msedge`, ou None.

    Une **lecture** : on regarde si l'exécutable est là, on ne le lance pas (la règle
    de la sonde du poste, `maestro.poste`). None veut dire « rien trouvé aux endroits
    où ces navigateurs s'installent », pas « rien d'installé » : le serveur garde alors
    son défaut, et sa propre erreur dira ce qui manque.

    `environ`, `plateforme` et `existe` sont injectables : la suite l'exerce sans
    navigateur ni système particulier.
    """
    env = os.environ if environ is None else environ
    systeme = sys.platform if plateforme is None else plateforme
    present = existe if existe is not None else _est_un_fichier
    for canal, emplacements in _CANAUX:
        for chemin in _chemins(canal, emplacements, env, systeme):
            if present(chemin):
                return canal
    return None


def _chemins(
    canal: str,
    emplacements: tuple[tuple[str, str], ...],
    env: Mapping[str, str],
    systeme: str,
) -> tuple[Path, ...]:
    if systeme == "win32":
        # Composés à la Windows quel que soit l'OS qui joue la règle : la suite la
        # joue aussi dans un conteneur Linux, où `/` joindrait un chemin de travers.
        # Par sa forme texte : `Path(PureWindowsPath(…))` en reprendrait les morceaux.
        return tuple(
            Path(str(PureWindowsPath(env[variable]) / relatif))
            for variable, relatif in emplacements
            if env.get(variable)
        )
    famille = "darwin" if systeme == "darwin" else "linux"
    return tuple(Path(chemin) for chemin in _CHEMINS_FIXES[famille].get(canal, ()))


def _est_un_fichier(chemin: Path) -> bool:
    try:
        return chemin.is_file()
    except OSError:  # pragma: no cover - chemin non représentable
        return False


def dossier_des_sorties(environ: Mapping[str, str] | None = None) -> Path:
    """`<temp>/maestro/navigateur` — où tombent les captures, hors de tout projet. Non créé."""
    return racine_jetable(environ) / NOM_SORTIES


def serveur_navigateur(
    registre: RegistreMcp | None = None,
    *,
    canal: str | None = None,
    sorties: Path | None = None,
) -> ServeurMcp:
    """Le serveur navigateur à monter : l'entrée de l'allowlist, et ce que le poste dit.

    Instancié par le registre (`RegistreMcp.instancier`) — c'est le garde-fou :
    une entrée retirée de l'allowlist lève ici, et rien n'est équipé. Les arguments
    du poste s'ajoutent **après** ceux de l'entrée, jamais à leur place.
    """
    serveur = (registre or RegistreMcp.curee()).instancier(ID_INTEGRATION, nom=NOM_SERVEUR)
    poste = [f"--output-dir={sorties if sorties is not None else dossier_des_sorties()}"]
    if canal:
        poste.insert(0, f"--browser={canal}")
    return replace(serveur, args=(*serveur.args, *poste))


def integration_navigateur(
    registre: RegistreMcp | None = None,
    *,
    canal: str | None = None,
    sorties: Path | None = None,
) -> IntegrationMcp:
    """L'intégration du pool d'un projet qui porte le navigateur de Maestro."""
    return IntegrationMcp(
        id=ID_INTEGRATION,
        serveur=serveur_navigateur(registre, canal=canal, sorties=sorties),
    )


def outils_du_navigateur() -> str:
    """Le préfixe des outils que l'agent voit — `mcp__navigateur__`."""
    return f"mcp__{NOM_SERVEUR}__"
