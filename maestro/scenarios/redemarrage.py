"""Couper l'API sous le banc, puis la rallumer sur les mêmes données (#1408).

S12 interrompt un run comme une personne le fait : elle le met en pause, ferme
Maestro, puis le rouvre. Le banc parle à l'API en HTTP (`maestro.scenarios.api`) et
ne possède pas son process — c'est `start.sh` ou le lanceur du produit qui l'a
démarrée. Ce module est la seule couture du banc qui touche au **process** de
l'API, et il ne réécrit aucun geste :

- **l'extinction** est la route de l'API (`POST /api/extinction`,
  `ClientAPI.eteindre`), celle que poussent les gestes d'arrêt avant de libérer les
  ports. Le scénario l'appelle lui-même : c'est elle qui solde les runs en vol, et
  c'est elle que le scénario mesure ;
- **couper le process** est le geste de `start.sh --couper-api` : les pids qui
  écoutent sur le port de l'API, terminés net. Une fois les runs soldés, `--stop`
  n'en fait pas d'autre (`arreter_session` → `liberer_port`). Le relevé des pids
  par port vit là, une fois, et n'est pas réécrit ici ;
- **rallumer** est la ligne de démarrage de `start.sh` — `python -m
  maestro.controltower.cli --port <port> [--etat-banc]` —, détachée comme le
  lanceur du produit détache ses services (`Systeme.demarrer`).

⚠ **Rallumer ne passe pas par `start.sh` entier.** Sur l'état du banc, le lanceur
réécrirait d'abord le journal (`maestro.scenarios.etat --rouvrir`) : l'API repartirait
sur l'état du dernier passage **sauvé**, et la reprise qu'on veut mesurer se ferait
sur un journal qui n'est plus celui où le run s'est interrompu. Seule l'API
redémarre, sur le journal vivant — exactement ce qu'un redémarrage de Maestro
rejoue (docs/28 §11). L'UI, que le banc n'emprunte pas, reste servie.

Le **jeu de données** se lit sur l'API avant de la couper : si elle sert l'espace du
banc de la copie, elle se rallume sur lui (`--etat-banc`), comme `banc.py` le lit
avant de sauver un état ; sinon sur celui de la copie.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from maestro.controltower.donnees import donnees_du_banc
from maestro.controltower.purge import port_api
from maestro.espace import racine_de_la_copie
from maestro.lanceur.lanceur import DELAI_API, DELAI_LIBERATION, HOTE_DEFAUT
from maestro.lanceur.systeme import Systeme
from maestro.sandbox import verification as execution
from maestro.scenarios.api import ClientAPI

#: Le module que `start.sh` démarre pour servir l'API — et l'option qui la place sur
#: le jeu de données du banc (`maestro.controltower.cli`).
MODULE_API = "maestro.controltower.cli"
OPTION_BANC = "--etat-banc"

#: Le lanceur de développement, dont `--couper-api` relève et termine les pids qui
#: écoutent sur le port de l'API (#1165).
LANCEUR = Path("scripts") / "controltower" / "start.sh"
GESTE_COUPER = "--couper-api"

#: Ce qu'on laisse au geste de coupe pour rendre la main : il relève des pids et en
#: termine un ou deux, rien de plus.
DELAI_COUPE_S = 60.0

#: L'intervalle entre deux sondes pendant qu'on attend l'API (morte, puis vivante).
INTERVALLE_SONDE_S = 0.5

#: L'encodage sous lequel la stack de Maestro tourne — celui que `start.sh` exporte
#: (#141) et que le banc pose déjà pour rejouer les commandes d'un projet.
ENCODAGE = ("PYTHONIOENCODING", "utf-8")


def redemarrer_l_api(
    client: ClientAPI,
    *,
    journal: Path,
    environnement: Mapping[str, str] | None = None,
    systeme: Systeme | None = None,
    couper: Callable[[int], None] | None = None,
    horloge: Callable[[], float] = time.monotonic,
    dormir: Callable[[float], None] = time.sleep,
) -> str:
    """Coupe l'API qui sert le banc et la rallume sur les mêmes données — rend ce qui a été fait.

    Lève `OSError` quand un geste n'aboutit pas : une API qui ne se coupe pas, un
    port qui ne se libère pas, une API rallumée qui ne répond pas. Un banc qui n'a
    pas pu rallumer Maestro ne mesure rien de la reprise — c'est un empêchement,
    jamais un rouge du produit.

    `systeme`, `couper`, `horloge` et `dormir` sont injectables pour la suite : le
    déroulé doit s'éprouver sans tuer ni lancer aucun process.
    """
    env = dict(os.environ if environnement is None else environnement)
    env.setdefault(*ENCODAGE)
    systeme = systeme or Systeme()
    port = port_api(env)
    banc = client.espace() == donnees_du_banc(environnement=env).espace.nom

    (couper or couper_l_api)(port)
    if not _attendre(lambda: not client.sante(), DELAI_LIBERATION, horloge, dormir):
        raise OSError(f"l'API répond encore sur :{port} une fois coupée")
    if not _attendre(
        lambda: systeme.attachable(HOTE_DEFAUT, port), DELAI_LIBERATION, horloge, dormir
    ):
        raise OSError(f"le port :{port} ne se libère pas une fois l'API coupée")

    argv = [sys.executable, "-m", MODULE_API, "--port", str(port)]
    if banc:
        argv.append(OPTION_BANC)
    systeme.demarrer(argv, cwd=racine_de_la_copie(), journal=journal, environ=env)
    if not _attendre(client.sante, DELAI_API, horloge, dormir):
        raise OSError(
            f"l'API rallumée ne répond pas sur :{port} en {DELAI_API:g} s — voir {journal}"
        )
    donnees = "l'état du banc" if banc else "les données de la copie"
    return f"API coupée puis rallumée sur :{port}, sur {donnees} — journal {journal}"


def couper_l_api(port: int) -> None:
    """`start.sh --couper-api` sur ce port, par le bash des agents — lève `OSError` s'il échoue.

    Joué comme le banc joue déjà les commandes d'un projet (`maestro.sandbox.
    verification.jouer`) : Git Bash sous Windows, jamais le `bash.exe` de WSL — c'est
    là que `start.sh` trouve ses outils (`netstat`, `taskkill`, ou `lsof`) —, et
    l'arbre arrêté si le geste ne rend pas la main.
    """
    interprete = execution.interprete()
    if interprete is None:
        raise OSError(f"aucun bash sur ce poste pour couper l'API (`start.sh {GESTE_COUPER}`)")
    commande = f"MAESTRO_PORT_API={port} bash {LANCEUR.as_posix()} {GESTE_COUPER}"
    fini = execution.jouer(
        commande, racine_de_la_copie(), interprete=interprete, delai_s=DELAI_COUPE_S
    )
    if fini.expiree:
        raise OSError(f"`start.sh {GESTE_COUPER}` n'a pas rendu la main en {DELAI_COUPE_S:g} s")
    if fini.code != 0:
        raise OSError(f"`start.sh {GESTE_COUPER}` sort en {fini.code} : {fini.sortie[-400:]}")


def _attendre(
    condition: Callable[[], bool],
    delai_s: float,
    horloge: Callable[[], float],
    dormir: Callable[[float], None],
) -> bool:
    """Vrai dès que `condition` l'est, faux si elle ne l'est pas devenue en `delai_s`."""
    limite = horloge() + delai_s
    while True:
        if condition():
            return True
        if horloge() >= limite:
            return False
        dormir(INTERVALLE_SONDE_S)
