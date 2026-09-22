"""La session du lanceur : ce qu'il a démarré, écrit pour celui qui l'arrêtera (#640).

Le geste d'arrêt n'est **pas** joué par le process qui a démarré : on lance Maestro
dans un terminal, on l'arrête depuis un autre, ou depuis la coque qui se ferme. Rien
ne voyage donc de l'un à l'autre en mémoire — d'où ce fichier, `.maestro/lanceur/
session.json`, seul état durable du lanceur.

Il porte le **strict nécessaire pour reconnaître** ce qu'il faut arrêter :

- le `pid` — nécessaire, jamais suffisant : un pid se recycle (#456), et tuer l'arbre
  d'un inconnu est ce que « rien n'est tué au jugé » (#213) interdit ;
- le `marqueur` — un fragment distinctif de la ligne de commande, le **témoin
  d'identité** qui départage un service vivant d'un pid recyclé ;
- le `port` et l'`url` — le second témoin, et ce qu'on sonde pour dire si le service
  répond encore ;
- le `journal` — en chemin **relatif** à la racine, parce que c'est là qu'on lit la
  cause quand un service est mort (troisième critère du ticket).

Il est écrit **dès que l'API est lancée**, avant même qu'elle réponde : une session
interrompue au milieu de son démarrage laisse alors de quoi la ramasser. Le lire ne
suppose jamais qu'il soit à jour — c'est un point de départ pour reconnaître, pas une
vérité sur l'état du poste.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

#: Version du fichier. Un jour où sa forme changera, un lanceur neuf saura qu'il lit
#: l'état d'un lanceur ancien plutôt que d'échouer sur une clé absente.
VERSION = 1


@dataclass(frozen=True)
class Service:
    """Un des deux services d'une stack, tel que le lanceur l'a démarré."""

    nom: str
    pid: int
    hote: str
    port: int
    marqueur: str
    journal: str
    sonde: str


@dataclass(frozen=True)
class Session:
    """La stack que le lanceur a montée — et qu'il saura défaire."""

    demarre_a: float
    url: str
    services: tuple[Service, ...] = ()
    version: int = VERSION

    def service(self, nom: str) -> Service | None:
        for service in self.services:
            if service.nom == nom:
                return service
        return None

    def avec(self, service: Service) -> Session:
        """La même session, ce service ajouté (ou remplacé s'il y était déjà)."""
        autres = tuple(s for s in self.services if s.nom != service.nom)
        return replace(self, services=(*autres, service))


def lire(chemin: Path) -> Session | None:
    """La session inscrite, ou `None` — fichier absent, illisible ou hors d'âge.

    Un fichier abîmé rend `None` plutôt qu'une exception : l'arrêt doit pouvoir se
    tenter quoi qu'il arrive, et « je ne sais pas ce qui tourne » se dit, alors que
    la trace d'un `json.JSONDecodeError` n'apprend rien à personne.
    """
    try:
        brut: Any = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(brut, dict) or brut.get("version") != VERSION:
        return None
    services = []
    for entree in brut.get("services", []):
        if not isinstance(entree, dict):
            return None
        try:
            services.append(Service(**entree))
        except TypeError:
            return None
    try:
        return Session(
            demarre_a=float(brut["demarre_a"]),
            url=str(brut["url"]),
            services=tuple(services),
        )
    except (KeyError, TypeError, ValueError):
        return None


def ecrire(chemin: Path, session: Session) -> None:
    """Inscrit la session. Le dossier est créé au besoin — c'est le premier passage."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        json.dumps(asdict(session), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def effacer(chemin: Path) -> None:
    """Retire l'inscription. Une session déjà absente n'est pas une erreur."""
    chemin.unlink(missing_ok=True)
