"""Les ports que Maestro donne aux serveurs lancés sur le poste (#1487, #1491).

Un port est au **poste**, pas à l'espace de travail : deux serveurs qui écoutent le
même se le disputent, quel que soit le dossier d'où ils partent. Maestro en donne
donc un à ceux qui en lancent : au service d'une vérification
(`maestro.sandbox.verification.port_de_verification`, #1487), et à l'agent d'une
tâche outillée pour essayer les siens (`port_d_essai`, #1491). Ce module porte ce
qu'ils partagent — constater qu'un port est libre, tirer jusqu'à en trouver un — et
ce qui n'est qu'à l'agent : la réservation.

## Libre, adresse par adresse

Un port est libre s'il l'est sur **chacune** des adresses où un serveur local
écoute (`ADRESSES_LOCALES`) : sous Windows, un `bind` sur `0.0.0.0` réussit à côté
d'un serveur qui n'écoute que `127.0.0.1` (mesuré sur le poste, #1487).

## Le port d'essai d'un agent est réservé, pas seulement tiré (#1491)

Le cadre de l'agent lui disait d'essayer son serveur sur « un port libre que tu
choisis », et il n'en recevait aucun : chacun tirait le sien de tête, et un modèle
tire mal au hasard. Mesuré sur le run `3e565990fca7` : trois tâches parallèles sur
quatre ont joué 47931, et les six lancements tenaient en 25 numéros consécutifs.
Constater le port libre ne suffit pas non plus : un voisin qui prend le même nombre
quelques secondes plus tard, ou dont le serveur n'écoute pas encore (15 s dans ce
projet), passe sous le constat.

L'agent reçoit donc **son** port, tiré dans `PORTS_D_ESSAI` et **réservé** pour la
durée de sa tâche. La réservation est un socket **UDP** lié au même numéro sur la
boucle locale, que Maestro tient tant que la tâche dure : les ports TCP et UDP sont
deux espaces distincts, si bien que le serveur que l'agent lance (TCP) n'en est
pas gêné, tandis qu'une autre réservation du même numéro — une tâche voisine, ou un
autre process de Maestro (un worker, un autre run) — échoue et tire ailleurs. Rien
n'est posé sur le disque, et le système rend le socket quand son détenteur meurt :
il n'y a pas de réservation orpheline à ramasser (#1454).

La plage est **voisine** de celle des vérifications, jamais la même : un port
d'essai ne croise donc jamais le port d'une vérification, sans que l'un ait à
constater l'autre.
"""

from __future__ import annotations

import errno
import random
import socket
from collections.abc import Iterator
from contextlib import contextmanager

#: Les adresses où un serveur local écoute, chacune constatée pour elle-même.
ADRESSES_LOCALES: tuple[tuple[socket.AddressFamily, str], ...] = (
    (socket.AF_INET, "127.0.0.1"),
    (socket.AF_INET, "0.0.0.0"),  # noqa: S104 - on constate, on n'écoute pas
    (socket.AF_INET6, "::1"),
    (socket.AF_INET6, "::"),
)

#: Combien de ports tirer avant de dire qu'aucun n'est libre : sur dix mille, un
#: poste où soixante-quatre tirages tombent tous sur un port tenu n'en a plus.
ESSAIS_PORT = 64

#: Où Maestro tire le port d'essai d'un agent (#1491). **Au-dessus** des ports qu'un
#: projet choisit de lui-même (3000, 4321, 8080…), **sous** ceux des vérifications
#: (`maestro.sandbox.verification.PORTS_DE_VERIFICATION`, 20 000 et au-delà), et
#: donc loin des ports que le système distribue de lui-même aux connexions sortantes.
PORTS_D_ESSAI = range(10_000, 20_000)

#: L'adresse où la réservation d'un port d'essai est tenue : la boucle locale, que
#: tout poste a, et où un socket lié n'ouvre rien vers l'extérieur.
ADRESSE_DE_RESERVATION = "127.0.0.1"


class PortIndisponible(OSError):
    """Aucun port libre n'a été trouvé dans une plage (#1487, #1491).

    Une `OSError` : c'est le poste qui n'a rien laissé, pas le service qui a échoué —
    à l'appelant de dire ce qu'il en fait.
    """


def tirages(
    plage: range, *, tirage: random.Random | None = None, essais: int = ESSAIS_PORT
) -> Iterator[int]:
    """`essais` ports tirés au hasard dans `plage`. `tirage` est la couture des tests."""
    hasard = tirage if tirage is not None else random.SystemRandom()
    for _ in range(essais):
        yield hasard.randrange(plage.start, plage.stop)


def port_libre(port: int) -> bool:
    """`port` est-il libre sur chaque adresse locale ? — un `bind` par adresse, aussitôt rendu.

    Une adresse que le poste n'a pas (IPv6 coupé) ne retient rien : personne n'y
    écoute. Tout autre refus — le port est tenu, ou réservé par le système (une plage
    d'Hyper-V répond « accès refusé ») — le dit pris : un autre tirage suivra.
    """
    for famille, adresse in ADRESSES_LOCALES:
        try:
            prise = socket.socket(famille, socket.SOCK_STREAM)
        except OSError:  # famille absente du poste
            continue
        with prise:
            try:
                prise.bind((adresse, port))
            except OSError as exc:
                if exc.errno == errno.EADDRNOTAVAIL:  # adresse absente du poste
                    continue
                return False
    return True


@contextmanager
def port_d_essai(
    *, tirage: random.Random | None = None, essais: int = ESSAIS_PORT
) -> Iterator[int]:
    """Le port d'essai d'un agent, réservé tant que le bloc dure (#1491).

    Tiré dans `PORTS_D_ESSAI`, rendu seulement s'il est libre (`port_libre`) **et**
    qu'aucune autre réservation ne le tient, dans ce process ou un autre (voir
    l'en-tête). La réservation est rendue en sortie du bloc, quelle qu'en soit
    l'issue. `tirage` est la couture des tests.

    Lève `PortIndisponible` si `essais` tirages n'en ont trouvé aucun.
    """
    for port in tirages(PORTS_D_ESSAI, tirage=tirage, essais=essais):
        reservation = _reserver(port)
        if reservation is None:
            continue
        with reservation:
            if not port_libre(port):
                continue
            yield port
            return
    raise PortIndisponible(
        f"aucun port d'essai libre parmi {essais} tirés entre "
        f"{PORTS_D_ESSAI.start} et {PORTS_D_ESSAI.stop - 1}"
    )


def _reserver(port: int) -> socket.socket | None:
    """Le socket UDP qui tient la réservation de `port` — `None` si une autre le tient déjà.

    Sans `SO_REUSEADDR` : c'est ce qui fait échouer une seconde réservation du même
    numéro. Non héritable (PEP 446) : les processus que la tâche lance ne le gardent
    pas après Maestro.
    """
    try:
        prise = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        prise.bind((ADRESSE_DE_RESERVATION, port))
    except OSError:
        prise.close()
        return None
    return prise


__all__ = [
    "ADRESSES_LOCALES",
    "ADRESSE_DE_RESERVATION",
    "ESSAIS_PORT",
    "PORTS_D_ESSAI",
    "PortIndisponible",
    "port_d_essai",
    "port_libre",
    "tirages",
]
