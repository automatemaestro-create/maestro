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
un profil tenu en mémoire (`--headless`, `--isolated`). Deux choses s'y ajoutent quand
on le pose au pool d'un projet, parce qu'elles ne se savent que sur le poste :

- **le navigateur qu'il pilote** (`navigateur_du_poste`) : Chrome s'il est installé,
  sinon Edge, sinon Chromium. Chrome et Edge sont des « canaux » que Playwright trouve
  et pilote sans rien télécharger ; Chromium n'en est pas un, il se désigne par son
  exécutable. Aucun : le serveur garde son défaut, et c'est sa propre erreur, lisible,
  que l'agent lira à son premier geste ;
- **où vont ses captures** (`dossier_des_sorties`) : sous le jetable du poste, jamais
  dans le dossier de la tâche. Son défaut est `.playwright-mcp/` dans le répertoire
  courant — l'espace de la tâche, dont tout fichier ressort en livrable et part dans
  le projet de la personne à la fusion.

## Ce qu'il écrit sous un nom que l'agent lui donne

`--output-dir` ne couvre que les noms que l'adaptateur choisit lui-même. Un nom que le
modèle donne (`filename` d'une capture, d'un instantané, d'un journal…), il le résout
contre son **espace de travail** : la racine que le client MCP annonce, c'est-à-dire
la tâche. Une capture nommée y tomberait (#1674).

Le point de contrôle des appels d'outil le pose donc sous le dossier de sorties avant
que l'appel parte (`RedirectionDesSorties`, appliquée par le hook du fournisseur). La
règle est ici, parce que c'est ici qu'on sait quels outils de l'adaptateur **écrivent**
sous ce nom (`OUTILS_QUI_ECRIVENT`) et lesquels **lisent** un fichier du projet, qui
doivent continuer de le lire. L'adaptateur n'écrit que sous deux racines, son dossier de
sorties et l'espace de la tâche, et refuse le reste :

- un nom **relatif** se pose sous le dossier de sorties, ses dossiers gardés ;
- un chemin absolu **dans l'espace de la tâche** aussi, au même chemin relatif : une
  capture que l'agent croit livrer à la personne lui revient par son chemin dans le
  compte rendu, jamais par un fichier du projet ;
- un chemin déjà sous le dossier de sorties, un chemin hors des deux racines et un nom
  relatif qui en sortirait (`..`) partent tels quels — l'adaptateur accepte le premier
  et refuse les autres.

Ce qui échappe au point de contrôle est nommé : du code Playwright passé à
`browser_run_code_unsafe` écrit où il veut (`page.screenshot({ path })`), et aucun
garde-fou ne lit un chemin dans un programme. La consigne de l'agent l'en détourne, et
le geste de capture lui donne de quoi s'en passer (`fullPage`).

**Une seule fabrique** (`integration_navigateur`) pour les deux chemins qui le posent :
l'équipe qui le reçoit à sa composition (`maestro.controltower.equipe`) et la personne
qui l'ajoute depuis la bibliothèque des intégrations (`POST /api/mcp/pool`). Les deux
reçoivent le même serveur, complété par le poste et sous le même nom (#1313).

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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Any

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
    "la capturer à une largeur donnée — `browser_resize`, puis `browser_take_screenshot` "
    "(`fullPage: true` pour la page entière) ;",
    "émuler la réduction des animations — `browser_run_code_unsafe` avec "
    "`async (page) => { await page.emulateMedia({ reducedMotion: 'reduce' }) }`, puis "
    "recharger la page ;",
    "lire les erreurs de la console — `browser_console_messages` (niveau `error`).",
)

#: Ce qui suit les gestes : la consigne que p5 a montrée nécessaire (docs/50 § 4.1).
#:
#: ⚠ Ses deux dernières phrases viennent de p5 **rejoué** avec le navigateur (run
#: `6193eadac741`, 2026-10-22) : le serveur range son journal de console dans un fichier
#: de son dossier de sorties, hors du projet, et y renvoie après chaque geste (« New
#: console entries : …log#L5-L11 »). Deux agents l'ont lu au shell (`sed -n`, `cat`), et
#: chaque lecture hors du projet a attendu qu'une personne tranche. L'outil `Read` le lit
#: sans rien demander, et `browser_console_messages` rend la console sans fichier.
#:
#: Ce qu'elle promet des noms tient au point de contrôle (`RedirectionDesSorties`) ; ce
#: qu'elle dit de `browser_run_code_unsafe` vient de l'exercice de #1674, où un agent a
#: capturé par du code, sous un chemin du projet, la capture que la personne voulait garder.
CONSIGNE_DE_L_AGENT = (
    "N'écris pas ton propre banc pour regarder une page : ni Chrome piloté par CDP, ni "
    "puppeteer ou Playwright installés pour l'occasion, ni fichier d'essai posé hors du "
    "projet. Le navigateur est déjà là, sans fenêtre, sous un profil qui ne laisse rien "
    "derrière lui, et il s'arrête avec ta tâche. Lance le serveur du projet, ouvre son "
    "adresse locale, regarde, puis arrête ce que tu as lancé. Ce que le navigateur "
    "écrit — captures, journal de la console — tombe hors du projet, que tu nommes le "
    "fichier ou non, même sous un chemin du projet : demande la console à "
    "`browser_console_messages`, lis un fichier qu'il te désigne avec l'outil `Read`, "
    "jamais au shell, où une lecture hors du projet attend qu'une personne tranche. Une "
    "capture qu'on te demande de garder se remet par son chemin dans ton compte rendu. "
    "Capture par `browser_take_screenshot`, jamais par du code passé à "
    "`browser_run_code_unsafe` qui écrirait un fichier : ce code écrit où il veut, dans "
    "le projet compris."
)

#: Le sous-dossier du jetable où tombent ses captures (voir l'en-tête).
NOM_SORTIES = "navigateur"

#: L'option qui déclare au serveur son dossier de sorties — posée au montage, relue par
#: la redirection : c'est le dossier que le serveur accepte, pas un autre.
OPTION_SORTIES = "--output-dir="

#: Le paquet de l'adaptateur, tel que l'entrée de l'allowlist le monte (`<paquet>@<version>`).
PAQUET_ADAPTATEUR = "@playwright/mcp"

#: L'argument par lequel l'agent nomme ce qu'un outil de l'adaptateur écrit.
ARGUMENT_NOM = "filename"

#: Les outils de l'adaptateur qui **écrivent** un fichier sous le nom que l'agent donne
#: (`ARGUMENT_NOM`) : captures, instantanés, journaux de console et de réseau, résultat
#: d'une évaluation, PDF, état de stockage, vidéo. Ceux qui **lisent** un fichier sous ce
#: nom n'y sont pas — `browser_run_code_unsafe`, `browser_set_storage_state`, et
#: `browser_file_upload` par ses `paths` — : ils doivent pouvoir donner au navigateur un
#: fichier du projet. Lu sur l'adaptateur que l'allowlist épingle ; une montée de version
#: se relit ici.
OUTILS_QUI_ECRIVENT: frozenset[str] = frozenset(
    {
        "browser_take_screenshot",
        "browser_snapshot",
        "browser_console_messages",
        "browser_network_requests",
        "browser_network_request",
        "browser_evaluate",
        "browser_pdf_save",
        "browser_storage_state",
        "browser_start_video",
    }
)

#: Les canaux de l'adaptateur : les navigateurs que Playwright cherche lui-même, aux
#: emplacements que lit la sonde, et que leur nom suffit à désigner (`--browser=`).
CANAUX_PLAYWRIGHT: frozenset[str] = frozenset({"chrome", "msedge"})

#: Les navigateurs que le serveur sait piloter sur le poste, dans l'ordre de
#: préférence, avec les exécutables qui trahissent leur présence sous Windows.
#: Chrome d'abord : c'est le canal par défaut du serveur, donc celui que ses exemples
#: supposent. Chromium en dernier : l'adaptateur ne le connaît pas pour canal — son
#: `--browser=chromium` vise un Chromium qu'il télécharge —, il le reçoit par son
#: exécutable.
_NAVIGATEURS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
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
    (
        "chromium",
        (
            ("LOCALAPPDATA", r"Chromium\Application\chrome.exe"),
            ("PROGRAMFILES", r"Chromium\Application\chrome.exe"),
            ("PROGRAMFILES(X86)", r"Chromium\Application\chrome.exe"),
        ),
    ),
)

#: Hors de Windows, les chemins d'installation sont fixes.
_CHEMINS_FIXES: dict[str, dict[str, tuple[str, ...]]] = {
    "darwin": {
        "chrome": ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",),
        "msedge": ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",),
        "chromium": ("/Applications/Chromium.app/Contents/MacOS/Chromium",),
    },
    "linux": {
        "chrome": ("/opt/google/chrome/chrome",),
        "msedge": ("/opt/microsoft/msedge/msedge",),
        "chromium": ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium"),
    },
}


@dataclass(frozen=True)
class NavigateurDuPoste:
    """Un navigateur trouvé sur le poste : son nom et l'exécutable qui l'a trahi."""

    nom: str
    chemin: Path

    def arguments(self) -> tuple[str, ...]:
        """Ce que le serveur reçoit pour piloter ce navigateur-là plutôt que son défaut.

        Un canal se désigne par son nom, et Playwright le retrouve aux mêmes
        emplacements que la sonde. Un navigateur qui n'en est pas un se désigne par
        son exécutable.
        """
        if self.nom in CANAUX_PLAYWRIGHT:
            return (f"--browser={self.nom}",)
        return (f"--executable-path={self.chemin}",)


def navigateur_du_poste(
    *,
    environ: Mapping[str, str] | None = None,
    plateforme: str | None = None,
    existe: Callable[[Path], bool] | None = None,
) -> NavigateurDuPoste | None:
    """Le navigateur que le serveur pilotera sur ce poste — Chrome, Edge, Chromium, ou None.

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
    for nom, emplacements in _NAVIGATEURS:
        for chemin in _chemins(nom, emplacements, env, systeme):
            if present(chemin):
                return NavigateurDuPoste(nom=nom, chemin=chemin)
    return None


def _chemins(
    nom: str,
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
    return tuple(Path(chemin) for chemin in _CHEMINS_FIXES[famille].get(nom, ()))


def _est_un_fichier(chemin: Path) -> bool:
    try:
        return chemin.is_file()
    except OSError:  # pragma: no cover - chemin non représentable
        return False


def dossier_des_sorties(environ: Mapping[str, str] | None = None) -> Path:
    """`<temp>/maestro/navigateur` — où tombent les captures, hors de tout projet. Non créé."""
    return racine_jetable(environ) / NOM_SORTIES


#: Ce qui trouve le navigateur du poste — `navigateur_du_poste`, ou ce qu'on injecte.
Sonde = Callable[[], NavigateurDuPoste | None]


def serveur_navigateur(
    registre: RegistreMcp | None = None,
    *,
    nom: str | None = None,
    sonde: Sonde | None = None,
    sorties: Path | None = None,
) -> ServeurMcp:
    """Le serveur navigateur à monter : l'entrée de l'allowlist, et ce que le poste dit.

    Instancié par le registre (`RegistreMcp.instancier`) — c'est le garde-fou :
    une entrée retirée de l'allowlist lève ici, et rien n'est équipé. Les arguments
    du poste s'ajoutent **après** ceux de l'entrée, jamais à leur place.

    `nom` nomme la liaison, `navigateur` sauf choix contraire. `sonde` trouve le
    navigateur du poste : `navigateur_du_poste` par défaut, cherché à l'appel, donc
    remplaçable par une suite qui ne doit pas dépendre du poste qui la joue.
    """
    serveur = (registre or RegistreMcp.curee()).instancier(
        ID_INTEGRATION, nom=nom or NOM_SERVEUR
    )
    trouve = (sonde or navigateur_du_poste)()
    poste = (
        *(trouve.arguments() if trouve is not None else ()),
        f"{OPTION_SORTIES}{sorties if sorties is not None else dossier_des_sorties()}",
    )
    return replace(serveur, args=(*serveur.args, *poste))


def integration_navigateur(
    registre: RegistreMcp | None = None,
    *,
    nom: str | None = None,
    sonde: Sonde | None = None,
    sorties: Path | None = None,
) -> IntegrationMcp:
    """L'intégration du pool d'un projet qui porte le navigateur de Maestro.

    La seule fabrique : une équipe la pose à sa composition, la bibliothèque des
    intégrations quand la personne l'ajoute.
    """
    return IntegrationMcp(
        id=ID_INTEGRATION,
        serveur=serveur_navigateur(registre, nom=nom, sonde=sonde, sorties=sorties),
    )


def outils_du_navigateur() -> str:
    """Le préfixe des outils que l'agent voit — `mcp__navigateur__`."""
    return f"mcp__{NOM_SERVEUR}__"


@dataclass(frozen=True)
class RedirectionDesSorties:
    """Ce qui pose sous son dossier de sorties un fichier que l'agent nomme (#1674).

    `dossiers` associe le préfixe d'outils d'un navigateur monté (`mcp__<liaison>__`)
    au dossier de sorties qu'il déclare ; `espace` est l'espace de la tâche, la racine
    contre laquelle l'adaptateur résout un nom. Le point de contrôle la consulte sur
    chaque appel qu'il laisse passer ; elle ne refuse rien, et ne rouvre rien de ce que
    la politique a refusé.
    """

    dossiers: Mapping[str, Path]
    espace: Path | None = None

    def entree(self, outil: str, entree: Mapping[str, Any]) -> dict[str, Any] | None:
        """L'entrée de l'appel, son nom posé sous le dossier de sorties — None : rien à changer.

        Seuls changent les outils qui écrivent (`OUTILS_QUI_ECRIVENT`), sous un nom qui
        tomberait dans l'espace de la tâche (voir l'en-tête).
        """
        for prefixe, dossier in self.dossiers.items():
            if not outil.startswith(prefixe):
                continue
            if outil.removeprefix(prefixe) not in OUTILS_QUI_ECRIVENT:
                return None
            destination = _sous_le_dossier(dossier, entree.get(ARGUMENT_NOM), self.espace)
            if destination is None:
                return None
            return {**entree, ARGUMENT_NOM: str(destination)}
        return None


def _sous_le_dossier(dossier: Path, nom: object, espace: Path | None) -> Path | None:
    """Où poser `nom` sous `dossier` — None s'il n'a pas à bouger.

    Un nom relatif s'y pose s'il y reste une fois joint ; un chemin absolu dans
    `espace`, au même chemin relatif. Un ancrage sans lecteur ou sans racine (`/x`,
    `C:x`) n'est ni l'un ni l'autre : sous Windows, il se résout contre le lecteur ou le
    répertoire courant du serveur, et l'adaptateur le borne lui-même à ses racines.
    """
    if not isinstance(nom, str) or not nom.strip():
        return None
    chemin = Path(nom)
    racine = Path(os.path.normpath(dossier))
    if chemin.is_absolute():
        absolu = Path(os.path.normpath(chemin))
        if espace is None or absolu.is_relative_to(racine):
            return None
        tache = Path(os.path.normpath(espace))
        if not absolu.is_relative_to(tache):
            return None
        chemin = absolu.relative_to(tache)
    elif chemin.anchor:
        return None
    destination = Path(os.path.normpath(racine / chemin))
    if destination == racine or not destination.is_relative_to(racine):
        return None
    return destination


def redirection_des_sorties(
    serveurs: Iterable[ServeurMcp], espace: Path | None = None
) -> RedirectionDesSorties | None:
    """La redirection d'une session qui monte le navigateur — None si elle n'en monte aucun.

    Un navigateur se reconnaît au paquet de l'adaptateur dans ses arguments, sous
    n'importe quel nom de liaison ; son dossier est celui qu'il déclare
    (`OPTION_SORTIES`). Sans dossier déclaré, on ne sait pas où il écrit, et rien
    n'est redirigé. `espace` est l'espace de travail de la session.
    """
    dossiers: dict[str, Path] = {}
    for serveur in serveurs:
        if not any(_est_l_adaptateur(argument) for argument in serveur.args):
            continue
        sorties = next(
            (a.removeprefix(OPTION_SORTIES) for a in serveur.args if a.startswith(OPTION_SORTIES)),
            "",
        )
        if sorties:
            dossiers[f"mcp__{serveur.nom}__"] = Path(sorties)
    return RedirectionDesSorties(dossiers, espace) if dossiers else None


def _est_l_adaptateur(argument: str) -> bool:
    return argument == PAQUET_ADAPTATEUR or argument.startswith(f"{PAQUET_ADAPTATEUR}@")
