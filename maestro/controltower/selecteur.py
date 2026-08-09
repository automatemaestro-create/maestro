"""Sélecteur de dossier **natif du poste**, ouvert par le backend (#278).

Un navigateur ne livre jamais de chemin absolu — ni par `<input type="file">`,
ni par glisser-déposer. C'est la contrainte qui a fait naître l'explorateur
servi par l'API (#223, `maestro.controltower.projets`). Mais le backend, lui,
**tourne déjà sur le poste** : il peut ouvrir le dialogue de dossier de l'OS et
rendre le chemin choisi. C'est le pas d'après, demandé par
[docs/24 §4.4](../../docs/24-projets-locaux-et-poste-de-travail.md) et sans
attendre l'enveloppe de bureau de la Phase 9 (décisions D3/D4, #218).

**Un confort, jamais un passage obligé.** L'explorateur de l'API reste là et
reste la voie complète : ce module ne fait qu'éviter un clic-à-clic quand il
peut. Toute indisponibilité se **dit** (`disponibilite()` rend un motif et une
phrase) au lieu de laisser un bouton mort — c'est ce que le mode serveur reçoit.

Trois garde-fous, parce qu'ouvrir une fenêtre depuis une requête HTTP est un
effet de bord sur la machine de quelqu'un :

1. **le poste, pas le réseau** — la requête doit venir de la boucle locale
   (`127.0.0.1`, `::1`). Un backend joint depuis une autre machine ouvrirait un
   dialogue *sur le serveur*, devant personne, et laisserait un client distant
   piloter l'écran d'un autre. C'est aussi ce qui distingue proprement le mode
   serveur du mode poste, sans réglage à tenir à jour ;
2. **un dialogue à la fois** — un verrou non bloquant. Sans lui, N requêtes
   empilent N fenêtres modales que personne n'a demandées ;
3. **une attente bornée** — au-delà de `DUREE_MAX_S`, le dialogue est tué et la
   requête rend un refus. Une fenêtre oubliée ne tient pas un worker en otage.

Le dialogue est un **sous-process de l'OS**, jamais une boîte Tk dans le process
du serveur : `tkinter` exige le thread principal (indisponible sous un worker
ASGI) et sa présence dépend de l'installation Python. PowerShell, `osascript` et
`zenity`/`kdialog` sont là où leur OS est là, et leur absence est une réponse
(`selecteur-sans-outil`) plutôt qu'une exception.

Ce sous-process a une conséquence qu'on paie à l'ouverture : **il n'est pas
l'application au premier plan**, et une fenêtre ouverte par un process
d'arrière-plan passe derrière (#311). Chaque script se charge donc de remonter
la sienne, à la manière de son OS — propriétaire `TopMost` sous Windows,
`activate` sous macOS. Sous X11/Wayland, `zenity` et `kdialog` n'ont pas de règle
équivalente et sortent devant d'eux-mêmes : rien à faire de ce côté.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - dialogue de l'OS, argv fixe, jamais de shell
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

#: Au-delà, le dialogue est tué et la requête refuse. Cinq minutes : de quoi
#: parcourir son disque sans qu'une fenêtre oubliée ne bloque le service.
DUREE_MAX_S = 300

#: Les hôtes qui valent « la requête vient du poste ». `localhost` y est parce
#: qu'un client peut arriver par ce nom sans que l'ASGI ait résolu l'adresse.
HOTES_DU_POSTE: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})

#: Le réglage `MAESTRO_SELECTEUR_NATIF` : `auto` laisse décider les garde-fous.
REGLAGES = ("auto", "1", "0")

#: Un seul dialogue à la fois, pour tout le process.
_verrou = threading.Lock()


class SelecteurRefuse(ValueError):
    """Le sélecteur natif refuse, **avec son motif** — au patron de `RacineRefusee`.

    `motif` est un code stable (`selecteur-hors-poste`, `selecteur-sans-outil`…) :
    c'est lui que l'écran affiche ou traduit, le message restant la phrase
    lisible. Hérite de `ValueError` pour être attrapé par le même `except` que
    les refus du service des projets.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


@dataclass(frozen=True)
class Outil:
    """Le programme qui sait ouvrir un dialogue de dossier sur cet OS."""

    nom: str
    binaire: str


def hote_du_poste(hote: str | None) -> bool:
    """`hote` est-il la boucle locale ? — le discriminant « poste » vs « serveur ».

    Un hôte inconnu (`None`, le cas d'un transport ASGI qui ne le donne pas) est
    traité comme **distant** : en cas de doute, on refuse d'ouvrir une fenêtre.
    """
    return (hote or "").strip().casefold() in HOTES_DU_POSTE


def outil_disponible() -> Outil | None:
    """Le dialogue de dossier de cet OS, ou `None` s'il n'y en a pas d'atteignable.

    Sous Linux, l'absence de session graphique (`DISPLAY`/`WAYLAND_DISPLAY`) vaut
    absence d'outil : `zenity` installé sur un serveur sans écran n'ouvrira rien
    et resterait bloqué jusqu'au délai maximum.
    """
    if sys.platform == "win32":
        for nom in ("powershell", "pwsh"):
            if binaire := shutil.which(nom):
                return Outil(nom, binaire)
        return None
    if sys.platform == "darwin":
        binaire = shutil.which("osascript")
        return Outil("osascript", binaire) if binaire else None
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    for nom in ("zenity", "kdialog"):
        if binaire := shutil.which(nom):
            return Outil(nom, binaire)
    return None


def disponibilite(*, poste: bool, reglage: str | None = None) -> dict[str, object]:
    """Le sélecteur natif est-il ouvrable ici et maintenant — et sinon, pourquoi ?

    C'est cette réponse que l'écran lit **avant** d'afficher quoi que ce soit :
    un bouton quand `disponible`, la phrase de `message` sinon. Jamais un bouton
    qui échouerait au clic, jamais un silence — l'explorateur servi par l'API
    reste dans les deux cas la voie complète.
    """
    if (reglage or "auto").strip() == "0":
        return _indisponible(
            "selecteur-desactive",
            "Sélecteur natif désactivé sur ce backend (MAESTRO_SELECTEUR_NATIF=0) — "
            "l'explorateur ci-dessous reste disponible.",
        )
    if not poste:
        return _indisponible(
            "selecteur-hors-poste",
            "Backend distant : le dialogue de l'OS s'ouvrirait sur le serveur, pas sur "
            "votre écran — parcourez les dossiers avec l'explorateur ci-dessous.",
        )
    outil = outil_disponible()
    if outil is None:
        return _indisponible(
            "selecteur-sans-outil",
            "Aucun dialogue de dossier sur ce poste (ni PowerShell, ni osascript, ni "
            "zenity/kdialog, ou pas de session graphique) — l'explorateur ci-dessous "
            "reste disponible.",
        )
    return {
        "disponible": True,
        "motif": None,
        "message": "Le dialogue de dossier de votre poste peut être ouvert.",
        "outil": outil.nom,
    }


def choisir_dossier(*, depart: str | None = None, titre: str = "") -> str | None:
    """Ouvre le dialogue de dossier de l'OS et rend le chemin choisi, `None` si annulé.

    L'annulation **n'est pas une erreur** : fermer la fenêtre est un geste
    normal, qui rend `None` et laisse l'appelant tranquille. Les vrais empêchements
    (aucun outil, dialogue déjà ouvert, attente dépassée) lèvent `SelecteurRefuse`
    motivé.

    Le chemin rendu est celui de l'OS, **non canonicalisé** : c'est à l'appelant
    de le confronter aux frontières d'EF-38 (`verifier_zone_interdite`,
    `valider_racine`). Ce module ouvre une fenêtre, il n'autorise rien.
    """
    outil = outil_disponible()
    if outil is None:
        raise SelecteurRefuse(
            "selecteur-sans-outil",
            "Aucun dialogue de dossier n'est disponible sur ce poste.",
        )
    if not _verrou.acquire(blocking=False):
        raise SelecteurRefuse(
            "selecteur-en-cours",
            "Un dialogue de dossier est déjà ouvert : terminez-le avant d'en ouvrir un autre.",
        )
    try:
        return _lancer(outil, depart=depart, titre=titre or "Choisir le dossier du projet")
    finally:
        _verrou.release()


# --- Interne -------------------------------------------------------------


def _indisponible(motif: str, message: str) -> dict[str, object]:
    """La forme d'une indisponibilité — motif court, phrase lisible, pas d'outil."""
    return {"disponible": False, "motif": motif, "message": message, "outil": None}


def _lancer(outil: Outil, *, depart: str | None, titre: str) -> str | None:
    """Le sous-process du dialogue, son verdict traduit en chemin (ou `None`).

    Le code de retour ne suffit pas à distinguer « annulé » de « cassé » sur les
    trois OS : `osascript` sort en 1 sur une annulation comme sur une erreur de
    script. C'est donc la **sortie vide** qui vaut annulation, et un code non nul
    avec une sortie vide reste une annulation — le cas nominal du bouton
    « Annuler ». Seul un empêchement franc (binaire absent, attente dépassée)
    lève.
    """
    argv = _argv(outil, depart=depart, titre=titre)
    try:
        acheve = subprocess.run(  # nosec B603 - argv fixe, shell=False, binaire résolu par which
            argv,
            capture_output=True,
            timeout=DUREE_MAX_S,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise SelecteurRefuse(
            "selecteur-expire",
            f"Dialogue de dossier laissé ouvert plus de {DUREE_MAX_S} s : abandonné.",
        ) from exc
    except OSError as exc:
        raise SelecteurRefuse(
            "selecteur-sans-outil",
            f"Le dialogue de dossier n'a pas pu démarrer ({outil.nom} : {exc}).",
        ) from exc
    chemin = (acheve.stdout or "").strip().strip('"')
    return chemin or None


def _argv(outil: Outil, *, depart: str | None, titre: str) -> list[str]:
    """La ligne de commande du dialogue, par outil — jamais de shell, argv fixe."""
    if outil.nom in ("powershell", "pwsh"):
        return [outil.binaire, "-NoProfile", "-STA", "-Command", _script_powershell(depart, titre)]
    if outil.nom == "osascript":
        return [outil.binaire, "-e", _script_osascript(depart, titre)]
    if outil.nom == "kdialog":
        return [outil.binaire, "--title", titre, "--getexistingdirectory", depart or os.getcwd()]
    argv = [outil.binaire, "--file-selection", "--directory", f"--title={titre}"]
    if depart:
        # Zenity veut un séparateur final pour comprendre « démarre *dans* ce dossier ».
        argv.append(f"--filename={depart.rstrip('/')}/")
    return argv


def _script_powershell(depart: str | None, titre: str) -> str:
    """Le `FolderBrowserDialog` de WinForms, en une commande.

    `-STA` est indispensable (les dialogues WinForms l'exigent) et l'encodage de
    sortie est forcé en UTF-8 : sans lui, un chemin accentué revient en mojibake
    sur une console cp1252, et c'est un chemin de dossier — il doit être exact à
    l'octet.

    **Le dialogue reçoit un propriétaire `TopMost`** (#311), et c'est tout le
    sujet : `ShowDialog()` sans propriétaire ouvrait la boîte *derrière* toutes
    les fenêtres. Windows interdit à un process d'arrière-plan — ce sous-process
    est un enfant d'uvicorn, le premier plan appartient au navigateur — de
    s'emparer du premier plan (restrictions `SetForegroundWindow`), et une boîte
    de dialogue n'a **pas de bouton dans la barre des tâches** : invisible, elle
    devenait aussi injoignable, tenait le verrou d'`choisir_dossier` et rendait
    le bouton mort pendant `DUREE_MAX_S`.

    On ne cherche donc pas à voler le premier plan, on passe à côté de la règle :
    une fenêtre `TopMost` reste au-dessus des autres sans être au premier plan,
    et **les fenêtres qu'elle possède sont topmost avec elle**. Le propriétaire
    est réduit à 1 px, transparent et hors barre des tâches — il n'existe que
    pour porter cette propriété, et `DoEvents` le matérialise avant que la boîte
    modale ne parte (sans quoi elle s'ouvrirait contre une fenêtre pas encore
    créée). Le clavier, lui, peut rester au navigateur : le premier clic sur la
    boîte le lui rend, ce qui est le comportement normal d'une fenêtre au-dessus.

    ⚠ **`TopMost` se pose après `Show()`, jamais avant** — c'est le piège de ce
    correctif, et il est silencieux. Mesuré sur le poste : posée avant, la
    propriété vaut bien `$true` côté objet mais n'atteint pas la fenêtre, dont
    l'`ExStyle` sort à `0x00090000` (`LAYERED|CONTROLPARENT`, **sans** le
    `WS_EX_TOPMOST` `0x8`) ; posée après, `0x00090008`. Une première version de
    ce script la posait avant : elle passait tous les tests d'écriture du script
    et rouvrait la boîte exactement là où elle était, derrière tout. Corollaire à
    ne pas défaire non plus : la poser **aussi** avant serait pire que de ne pas
    la poser, le setter court-circuitant sur une valeur inchangée — l'appel
    d'après `Show()` ne ferait alors plus rien.

    `Width`/`Height` sont des entiers : les poser évite d'avoir à charger
    `System.Drawing` pour un `Size`, une dépendance de plus pour un carré d'un
    pixel.
    """
    depart_ps = _litteral_powershell(str(Path(depart)) if depart else "")
    return (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$p=New-Object System.Windows.Forms.Form;"
        "$p.ShowInTaskbar=$false;$p.Opacity=0;"
        "$p.FormBorderStyle=[System.Windows.Forms.FormBorderStyle]::None;"
        "$p.Width=1;$p.Height=1;"
        "$p.Show();"
        "$p.TopMost=$true;"
        "$p.Activate();[System.Windows.Forms.Application]::DoEvents();"
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.Description={_litteral_powershell(titre)};"
        "$d.ShowNewFolderButton=$true;"
        f"$s={depart_ps};"
        "if($s -and (Test-Path -LiteralPath $s)){$d.SelectedPath=$s};"
        "$r=$d.ShowDialog($p);"
        "$p.Close();"
        "if($r -eq [System.Windows.Forms.DialogResult]::OK)"
        "{[Console]::Out.Write($d.SelectedPath)}"
    )


def _script_osascript(depart: str | None, titre: str) -> str:
    """Le `choose folder` d'AppleScript, chemin rendu en POSIX.

    L'`activate` de tête est le pendant macOS du propriétaire `TopMost` de
    Windows (#311) : sans lui, `choose folder` s'ouvre derrière l'application
    active, pour la même raison — le process est lancé par le backend, pas par
    l'utilisateur. macOS, lui, laisse une application se mettre au premier plan
    sur sa propre demande, donc l'`activate` suffit et il n'y a pas de fenêtre
    porteuse à fabriquer.
    """
    defaut = f' default location POSIX file "{_echappe_applescript(depart)}"' if depart else ""
    return (
        "activate\n"
        f'POSIX path of (choose folder with prompt "{_echappe_applescript(titre)}"{defaut})'
    )


def _litteral_powershell(valeur: str) -> str:
    """`valeur` en chaîne PowerShell littérale — les quotes simples y sont doublées."""
    return "'" + valeur.replace("'", "''") + "'"


def _echappe_applescript(valeur: str) -> str:
    """`valeur` dans une chaîne AppleScript — antislashs et guillemets échappés."""
    return valeur.replace("\\", "\\\\").replace('"', '\\"')
