"""Les écoutes TCP du poste Windows, lues dans la table du système qui les porte (#1696).

    <python du venv> scripts/controltower/ecoutes.py

Une ligne par écoute, `<port> <pid>`, triées et sans doublon, IPv4 et IPv6 ensemble. Appelé par
`etat_stack_ecoutes` (`scripts/controltower/etat-stack.sh`), jamais à la main.

La table est celle de `GetExtendedTcpTable` dans sa classe `TCP_TABLE_OWNER_PID_LISTENER` : le
système y range chaque socket en écoute avec le pid qui la tient, quel que soit l'état qu'il lui
donne. `netstat` n'affiche une écoute que dans l'état `LISTENING` (2), or un poste peut porter les
siennes dans un état 0, hors de `MIB_TCP_STATE` : `netstat` ne les montre pas (#1579).

Bibliothèque standard seule, et rien de `maestro` : la lecture ne dépend que du système.

Codes de sortie : `0` table lue, vide comprise · `1` lecture refusée par le système, la cause sur
l'erreur standard · `3` hors de Windows, où `/proc` et `lsof` sont la source.
"""

from __future__ import annotations

import ctypes
import socket
import sys
from ctypes import wintypes

AF_INET = 2
AF_INET6 = 23
TCP_TABLE_OWNER_PID_LISTENER = 3

NO_ERROR = 0
ERROR_NOT_SUPPORTED = 50
ERROR_INSUFFICIENT_BUFFER = 122

#: La table peut grandir entre l'appel qui donne sa taille et celui qui la lit : on reprend avec
#: la taille nouvelle, quelques fois au plus.
ESSAIS = 8

#: L'en-tête d'une table : `dwNumEntries`, suivi des lignes, alignées sur un DWORD.
EN_TETE = ctypes.sizeof(wintypes.DWORD)


class LigneIPv4(ctypes.Structure):
    """`MIB_TCPROW_OWNER_PID`."""

    _fields_ = [
        ("etat", wintypes.DWORD),
        ("adresse_locale", wintypes.DWORD),
        ("port_local", wintypes.DWORD),
        ("adresse_distante", wintypes.DWORD),
        ("port_distant", wintypes.DWORD),
        ("pid", wintypes.DWORD),
    ]


class LigneIPv6(ctypes.Structure):
    """`MIB_TCP6ROW_OWNER_PID`."""

    _fields_ = [
        ("adresse_locale", ctypes.c_ubyte * 16),
        ("portee_locale", wintypes.DWORD),
        ("port_local", wintypes.DWORD),
        ("adresse_distante", ctypes.c_ubyte * 16),
        ("portee_distante", wintypes.DWORD),
        ("port_distant", wintypes.DWORD),
        ("etat", wintypes.DWORD),
        ("pid", wintypes.DWORD),
    ]


def lire_famille(famille: int, ligne: type[LigneIPv4] | type[LigneIPv6]) -> list[tuple[int, int]]:
    """Les écoutes d'une famille d'adresses, `(port, pid)`. Lève `OSError` sur un refus."""
    fonction = ctypes.WinDLL("iphlpapi.dll").GetExtendedTcpTable  # type: ignore[attr-defined]
    fonction.restype = wintypes.DWORD
    fonction.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    ]
    taille = wintypes.DWORD(0)
    tampon: ctypes.Array[ctypes.c_char] | None = None
    for _ in range(ESSAIS):
        code = fonction(
            tampon, ctypes.byref(taille), False, famille, TCP_TABLE_OWNER_PID_LISTENER, 0
        )
        if code == NO_ERROR:
            break
        if code == ERROR_NOT_SUPPORTED and famille == AF_INET6:
            # Une pile IPv6 absente ne porte aucune écoute IPv6.
            return []
        if code != ERROR_INSUFFICIENT_BUFFER:
            raise OSError(code, f"GetExtendedTcpTable (famille {famille}) a rendu {code}")
        tampon = ctypes.create_string_buffer(taille.value)
    else:
        raise OSError(ERROR_INSUFFICIENT_BUFFER, "la table grandit plus vite qu'elle ne se lit")
    if tampon is None:
        return []
    nombre = wintypes.DWORD.from_buffer(tampon).value
    if EN_TETE + nombre * ctypes.sizeof(ligne) > len(tampon):
        raise OSError(0, f"table tronquée : {nombre} lignes annoncées dans {len(tampon)} octets")
    lignes = (ligne * nombre).from_buffer(tampon, EN_TETE)
    return [(socket.ntohs(rangee.port_local & 0xFFFF), int(rangee.pid)) for rangee in lignes]


def main() -> int:
    if sys.platform != "win32":
        print("hors de Windows : /proc et lsof sont la source", file=sys.stderr)
        return 3
    try:
        ecoutes = sorted({*lire_famille(AF_INET, LigneIPv4), *lire_famille(AF_INET6, LigneIPv6)})
    except OSError as erreur:
        print(f"GetExtendedTcpTable refusé : {erreur}", file=sys.stderr)
        return 1
    # En octets : la sortie texte de Windows écrirait `\r\n`, que le shell garderait.
    sys.stdout.buffer.write("".join(f"{port} {pid}\n" for port, pid in ecoutes).encode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
