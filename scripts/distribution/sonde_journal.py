"""Ce que porte le journal local d'une installation, pour le retrouver après sa mise à jour (#644).

    <installation>\\resources\\produit\\runtime\\python\\python.exe sonde_journal.py [<rang>]

Joué par `scripts/distribution/verifier.sh --mise-a-jour`, avant puis après la mise à jour, avec
l'interpréteur **embarqué** : il lit `~/.maestro/*.sqlite3` — le journal que l'état du poste
garde hors du dossier d'installation (#1621) — **en lecture seule**, sans rien y écrire ni
migrer. Il rend une ligne par fichier :

    <nom> schema=<user_version> evenements=<nombre> dernier=<rang> empreinte=<sha256>

L'empreinte porte sur les événements de rang inférieur ou égal à `<rang>` (tous, sans lui) :
relue après la mise à jour jusqu'au dernier rang d'avant, elle dit si l'historique est resté le
même, octet pour octet. Ce que la nouvelle version consigne ensuite n'y entre pas.

Codes : `0` au moins un journal lu · `1` aucun journal, ou un journal illisible (la cause est dite).
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path


def lire(fichier: Path, jusqu_a: int | None) -> str:
    connexion = sqlite3.connect(f"{fichier.as_uri()}?mode=ro", uri=True)
    try:
        schema = connexion.execute("PRAGMA user_version").fetchone()[0]
        nombre, dernier = connexion.execute(
            "SELECT count(*), coalesce(max(rang), 0) FROM evenements"
        ).fetchone()
        borne = dernier if jusqu_a is None else jusqu_a
        empreinte = hashlib.sha256()
        for rang, charge in connexion.execute(
            "SELECT rang, charge FROM evenements WHERE rang <= ? ORDER BY rang", (borne,)
        ):
            empreinte.update(f"{rang}\t{charge}\n".encode())
    finally:
        connexion.close()
    return (
        f"{fichier.name} schema={schema} evenements={nombre} dernier={dernier} "
        f"empreinte={empreinte.hexdigest()}"
    )


def main(arguments: list[str]) -> int:
    jusqu_a = int(arguments[0]) if arguments else None
    fichiers = sorted((Path.home() / ".maestro").glob("*.sqlite3"))
    if not fichiers:
        print(f"aucun journal sous {Path.home() / '.maestro'}")
        return 1
    code = 0
    for fichier in fichiers:
        try:
            print(lire(fichier, jusqu_a))
        except sqlite3.Error as erreur:
            print(f"{fichier.name} illisible — {type(erreur).__name__}: {erreur}")
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
