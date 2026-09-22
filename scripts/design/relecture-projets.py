"""Les projets d'une stack de relecture, lus et déclarés par l'API RÉELLE (#1165).

    python scripts/design/relecture-projets.py lister   --port <api>
    python scripts/design/relecture-projets.py declarer --port <api> --racine <dossier> [--nom …]

Appelé par `scripts/design/relecture-visuelle.sh`, jamais à la main.

Sans projet actif, le shell ne rend que sa porte d'entrée (#279) : pour regarder un
écran, la session pose l'identifiant d'un projet dans le `localStorage` de chaque
origine. Du temps de la démo, cet identifiant était une constante et sa
déclaration un fichier écrit à la main. Sur la vraie stack, il vient de l'API :

- **lister** — les projets que la stack sert (l'état du banc en porte un par
  scénario joué), chacun avec son nombre de runs : c'est ce qui dit dans lequel un
  écran a quelque chose à montrer. Une ligne par projet,
  `id <TAB> nom <TAB> runs <TAB> racine`, dans l'ordre de l'API.
- **declarer** — le projet neuf de l'état « vide » : `POST /api/projets` avec
  `origine: "nouveau"`, donc la validation de racine du produit (EF-38) et le
  dossier créé par lui, comme pour quelqu'un qui démarre. Rend l'identifiant
  engendré — il diffère d'une stack à l'autre, et c'est à dire.

Rien n'est écrit ailleurs que par l'API, et rien n'est lu ailleurs qu'en elle : ce
module ne connaît ni un dépôt de fichiers ni une clé Redis. Bibliothèque standard
seule, comme `planche.py` : un poste sans venv le fait tourner.

Codes de sortie : `0` fait · `1` API injoignable · `2` usage · `3` refusé par l'API
(racine refusée : le motif est imprimé).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

#: Le délai d'une requête : l'API est locale, et une API qui ne répond pas en dix
#: secondes n'est pas prête à être regardée.
DELAI_S = 10.0

#: Le nom du projet neuf — ce que l'écran affiche, en français comme le reste.
NOM_DEFAUT = "Projet neuf"

CODE_FAIT = 0
CODE_INJOIGNABLE = 1
CODE_REFUS = 3


def _appel(port: int, methode: str, chemin: str, corps: dict[str, Any] | None = None) -> Any:
    donnees = None if corps is None else json.dumps(corps).encode("utf-8")
    requete = Request(  # noqa: S310 - l'URL est locale et construite ici
        f"http://127.0.0.1:{port}{chemin}",
        data=donnees,
        method=methode,
        headers={"Content-Type": "application/json"} if donnees is not None else {},
    )
    with urlopen(requete, timeout=DELAI_S) as reponse:  # noqa: S310
        return json.loads(reponse.read().decode("utf-8") or "null")


def _texte(valeur: Any) -> str:
    """Une cellule de TSV : ni tabulation ni saut de ligne, quoi que l'API rende."""
    return " ".join(str(valeur if valeur is not None else "").split())


def lister(port: int) -> int:
    projets = _appel(port, "GET", "/api/projets")
    for projet in projets if isinstance(projets, list) else []:
        identifiant = str(projet.get("id") or "")
        if not identifiant:
            continue
        try:
            runs = _appel(port, "GET", f"/api/executions?projet={quote(identifiant)}")
            nombre = str(len(runs)) if isinstance(runs, list) else "?"
        except (HTTPError, URLError, OSError, ValueError):
            nombre = "?"
        print(
            "\t".join(
                (identifiant, _texte(projet.get("nom")), nombre, _texte(projet.get("racine")))
            )
        )
    return CODE_FAIT


def declarer(port: int, racine: str, nom: str) -> int:
    try:
        projet = _appel(
            port, "POST", "/api/projets", {"nom": nom, "racine": racine, "origine": "nouveau"}
        )
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8") or "{}").get("detail")
        except ValueError:
            detail = None
        motif = detail.get("message") if isinstance(detail, dict) else detail
        print(f"refusé par l'API ({exc.code}) : {motif or exc.reason}", file=sys.stderr)
        return CODE_REFUS
    print(str(projet.get("id") or "") if isinstance(projet, dict) else "")
    return CODE_FAIT


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    verbes = parseur.add_subparsers(dest="verbe", required=True)
    liste = verbes.add_parser("lister")
    liste.add_argument("--port", type=int, required=True)
    decl = verbes.add_parser("declarer")
    decl.add_argument("--port", type=int, required=True)
    decl.add_argument("--racine", required=True)
    decl.add_argument("--nom", default=NOM_DEFAUT)
    args = parseur.parse_args(argv)
    try:
        if args.verbe == "lister":
            return lister(args.port)
        return declarer(args.port, args.racine, args.nom)
    except HTTPError as exc:
        print(f"l'API a répondu {exc.code} sur :{args.port}", file=sys.stderr)
        return CODE_INJOIGNABLE
    except (URLError, OSError, ValueError) as exc:
        print(f"API injoignable sur :{args.port} ({exc})", file=sys.stderr)
        return CODE_INJOIGNABLE


if __name__ == "__main__":
    raise SystemExit(main())
