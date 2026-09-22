"""La ligne de commande du lanceur — `maestro-lanceur`, `python -m maestro.lanceur` (#640).

Une couche mince, et qui le reste : elle lit des arguments, résout l'emplacement, et
appelle l'un des quatre gestes de `maestro.lanceur.lanceur`. Rien ne se décide ici.

**Deux noms pour la même chose, à dessein.** Une installation expose `maestro-lanceur`
(le point d'entrée déclaré dans `pyproject.toml`) ; un clone, qui n'a pas forcément
réinstallé le paquet, appelle `python -m maestro.lanceur`. Les deux chemins mènent à
`main`, et aucun texte du produit ne promet l'un sans l'autre.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from maestro.lanceur.emplacement import emplacement as resoudre_emplacement
from maestro.lanceur.lanceur import (
    HOTE_DEFAUT,
    PORT_API_DEFAUT,
    PORT_UI_DEFAUT,
    Options,
    arreter,
    demarrer,
    diagnostic,
    etat,
)
from maestro.lanceur.systeme import Sortie, Systeme

USAGE = """Maestro — démarrer et arrêter le produit d'un seul geste.

  maestro-lanceur                  démarre l'API et le front, puis ouvre le navigateur
  maestro-lanceur --no-browser     démarre et rend la main sans ouvrir de navigateur
  maestro-lanceur --stop           arrête tout, en soldant les runs en vol
  maestro-lanceur --etat           ce qui tourne ; pour ce qui est mort, sa cause
  maestro-lanceur --diagnostic     ce qui serait servi, sans rien démarrer

Options :
  --port-api <port>   port de l'API (défaut 8000, ou MAESTRO_PORT_API)
  --port-ui <port>    port du front (défaut 3000, ou MAESTRO_PORT_UI)
  --hote <adresse>    adresse d'écoute (défaut 127.0.0.1)

Codes : 0 fait · 1 panne nommée · 2 usage · 3 rien à faire · 4 port de l'API occupé.

Dans un clone, la même commande s'écrit : python -m maestro.lanceur [options]."""


class UsageRefuse(Exception):
    """Un argument que la ligne de commande ne sait pas lire."""


def analyser(argv: Sequence[str], environ: dict[str, str]) -> Options:
    """Les arguments et l'environnement, résolus en `Options`.

    Les variables `MAESTRO_PORT_API` / `MAESTRO_PORT_UI` donnent les défauts — ce sont
    celles que `scripts/git/worktree.sh ensure` pose par copie de travail (#152), et
    les ignorer ferait démarrer deux stacks sur les mêmes ports depuis deux worktrees.
    L'option, elle, passe devant.
    """
    action = "demarrer"
    navigateur = True
    hote = environ.get("MAESTRO_HOTE", "").strip() or HOTE_DEFAUT
    port_api = _port_environ(environ, "MAESTRO_PORT_API", PORT_API_DEFAUT)
    port_ui = _port_environ(environ, "MAESTRO_PORT_UI", PORT_UI_DEFAUT)

    reste = list(argv)
    while reste:
        argument = reste.pop(0)
        if argument in {"--stop", "--arreter"}:
            action = _geste(action, "arreter")
        elif argument == "--etat":
            action = _geste(action, "etat")
        elif argument == "--diagnostic":
            action = _geste(action, "diagnostic")
        elif argument in {"--no-browser", "--sans-navigateur"}:
            navigateur = False
        elif argument == "--port-api":
            port_api = _entier(_valeur(reste, argument), argument)
        elif argument == "--port-ui":
            port_ui = _entier(_valeur(reste, argument), argument)
        elif argument == "--hote":
            hote = _valeur(reste, argument)
        else:
            raise UsageRefuse(f"option inconnue : {argument}")

    return Options(
        action=action,
        navigateur=navigateur,
        hote=hote,
        port_api=port_api,
        port_ui=port_ui,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : `maestro-lanceur` et `python -m maestro.lanceur`."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    sortie = Sortie()
    if any(argument in {"-h", "--help", "--aide"} for argument in arguments):
        sortie.dire(USAGE)
        return 0
    environ = dict(os.environ)
    try:
        options = analyser(arguments, environ)
    except UsageRefuse as refus:
        sortie.alerter(str(refus))
        sortie.alerter("")
        sortie.alerter(USAGE)
        return 2

    systeme = Systeme()
    emplacement = resoudre_emplacement(environ)
    if options.action == "arreter":
        return arreter(
            options, emplacement=emplacement, systeme=systeme, sortie=sortie, environ=environ
        )
    if options.action == "etat":
        return etat(
            options, emplacement=emplacement, systeme=systeme, sortie=sortie, environ=environ
        )
    if options.action == "diagnostic":
        return diagnostic(
            options, emplacement=emplacement, systeme=systeme, sortie=sortie, environ=environ
        )
    return demarrer(
        options, emplacement=emplacement, systeme=systeme, sortie=sortie, environ=environ
    )


def _port_environ(environ: dict[str, str], cle: str, defaut: int) -> int:
    brut = environ.get(cle, "").strip()
    if not brut:
        return defaut
    return _entier(brut, cle)


def _entier(valeur: str, quoi: str) -> int:
    try:
        port = int(valeur)
    except ValueError as erreur:
        raise UsageRefuse(f"{quoi} attend un nombre : {valeur}") from erreur
    if not 1 <= port <= 65535:
        raise UsageRefuse(f"{quoi} hors bornes : {port}")
    return port


def _valeur(reste: list[str], argument: str) -> str:
    if not reste:
        raise UsageRefuse(f"{argument} attend une valeur")
    return reste.pop(0)


def _geste(courant: str, demande: str) -> str:
    """Un seul geste par appel : démarrer et arrêter dans la même commande n'a pas de sens."""
    if courant not in {"demarrer", demande}:
        raise UsageRefuse(f"un seul geste à la fois : {courant} ou {demande}")
    return demande
