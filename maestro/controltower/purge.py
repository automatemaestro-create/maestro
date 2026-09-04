"""Purge de l'état d'exécution de la Control Tower — rendre le poste vide (#853).

    .venv/Scripts/python.exe -m maestro.controltower.purge [--check] [--projets]

Jusqu'ici **aucun geste ne vidait le poste** : aucune route `DELETE` sur les
exécutions, et `start.sh --stop` solde les runs en vol sans rien effacer
(docs/28 §11). Chaque essai partait donc d'un historique — runs passés, coûts,
journal, conversations —, c'est-à-dire de la première chose qu'un nouvel
utilisateur ne voit pas. C'est le prérequis de `/retex-utilisateur`, qui joue
le rôle d'un utilisateur qui **découvre** Maestro : il lui faut le poste tel
qu'il est au premier démarrage (`PosteVide`).

**Ce que la purge vide : l'état d'exécution, et rien d'autre.**

- le **journal durable** (`persistence.py`) — la liste Redis d'où runs, tâches,
  validations, coûts et journal sont rejoués au démarrage de l'API : c'est lui
  qui fait qu'un redémarrage n'oublie rien, donc lui qu'il faut vider pour
  qu'un poste oublie ;
- les **battements** des runs (`battement.py`) — le hash où chaque hôte pose son
  signal de vie, sans lequel un run purgé laisserait derrière lui une entrée
  définitive dans un hash relu à chaque `GET /api/executions` ;
- la **file de tâches** (`celery_app.py`) et les **boîtes aux lettres** avec leur
  canal de diffusion (`mailbox.py`) — par prudence pour ces dernières : un
  pub/sub ne persiste rien, donc il n'y a le plus souvent aucune clé à retirer,
  et le compte rendu le dit ;
- les **conversations** du chat (`MAESTRO_CHAT_DIR`, défaut `core/chat/`) et les
  **téléversements** (`MAESTRO_INGESTION_DIR`, défaut `core/ingestion/`) ;
- avec `--projets`, les **déclarations** de projets (`MAESTRO_PROJETS_DIR`, un
  `<id>.json` par projet) — et **jamais** un dossier de projet sur le disque :
  la déclaration dit où est le projet, elle n'est pas le projet.

**Ce qu'elle ne touche jamais : la configuration** — agents, playbooks,
surcharges, capacités, permissions, secrets, serveurs MCP. Un poste vide n'est
pas un poste désinstallé : ce qu'un utilisateur a réglé reste réglé.

⚠ **Les clés et les dossiers viennent des constantes Python, jamais recopiés.**
Une constante recopiée des deux côtés d'une frontière est ce que #830 a vu
casser : `captures.mjs` a attendu un mois un texte que l'UI ne rendait plus.
Ici la purge **importe** `CLE_JOURNAL_EVENEMENTS`, `CLE_BATTEMENTS`,
`FILE_TACHES`, `CANAL_BOITE_PREFIXE`, `CANAL_DIFFUSION`, et résout les dossiers
par les mêmes `default()` / `racine_ingestion()` que l'API — le jour où l'un
d'eux bouge, la purge suit sans qu'on y pense. `tests/test_retex_utilisateur.py`
le garde : aucun littéral `maestro.` en dur dans ce module hors docstring.

**Elle refuse tant que l'API répond ou qu'un hôte détaché vit.** Leçon de #699 :
l'événement est consigné **là où il naît**, par le producteur et non par l'API —
un run en vol republierait donc dans le journal qu'on vient de vider, et une API
vivante relit le sien en mémoire et continuerait de le servir. Le geste
préalable est `bash scripts/controltower/start.sh --stop`, qui solde les runs
et éteint les hôtes avec leur descendance ; le refus le nomme. La vitalité d'un
hôte se lit au registre des battements (`vitalite`, #348) : ce qui bat est
vivant et bloque, ce qui ne bat plus depuis le seuil est orphelin et ne bloque
pas — c'est précisément ce qu'on veut ramasser.

`--check` dit ce qui partirait **sans rien écrire** — les mêmes comptes que le
réel rend une fois fait —, et rend le même verdict de refus, pour qu'on sache
avant de confirmer si le geste passerait. Codes de sortie : `0` fait (ou
vérifié), `3` refusé (API vivante ou hôte détaché en vol), `1` Redis
injoignable, `2` usage.

⚠ **La purge est destructive.** `/retex-utilisateur` ne la joue jamais d'office :
`--check` d'abord, puis un « oui » explicite, comme le feu vert de `/orchestrate`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TextIO
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from maestro.config import Settings, load_settings
from maestro.controltower.battement import CLE_BATTEMENTS, VITALITE_VIVANT, vitalite
from maestro.controltower.chat import ChatStore
from maestro.controltower.cli import PORT_DEFAUT
from maestro.controltower.events import REDIS_URL_DEFAUT
from maestro.controltower.persistence import CLE_JOURNAL_EVENEMENTS
from maestro.controltower.state import EXECUTION_EN_COURS
from maestro.messaging.mailbox import CANAL_BOITE_PREFIXE, CANAL_DIFFUSION
from maestro.projets.store import ProjetStore
from maestro.queue.celery_app import FILE_TACHES
from maestro.sources.resolution import racine_ingestion

#: Le nom sous lequel ce module s'invoque (`python -m …`) — dérivé, jamais écrit.
MODULE = __spec__.name if __spec__ is not None else __name__

_USAGE = f"Usage : python -m {MODULE} [--check] [--projets]"

#: La variable qui porte le port de l'API — le contrat de `scripts/controltower/start.sh`
#: (`MAESTRO_PORT_API`, défaut 8000), que `worktree.sh ensure` surcharge par worktree.
VARIABLE_PORT_API = "MAESTRO_PORT_API"

#: Délai de la sonde HTTP, en secondes : une API arrêtée refuse la connexion tout
#: de suite, et une API vivante répond en millisecondes sur `/api/sante`.
DELAI_SONDE_S = 2.0

#: Le geste préalable, nommé dans chaque refus : c'est lui qui solde les runs en
#: vol et éteint les hôtes détachés avec leur descendance (docs/28 §11).
GESTE_PREALABLE = "bash scripts/controltower/start.sh --stop"

#: Ce qu'un dossier de données garde à travers la purge : sa propre documentation,
#: versionnée dans le dépôt (`core/chat/README.md`, `core/ingestion/.gitignore`…).
#: Tout le reste est de la donnée d'exécution.
FICHIERS_CONSERVES = frozenset({"README.md", ".gitignore"})

CODE_FAIT = 0
CODE_REDIS_INJOIGNABLE = 1
CODE_USAGE = 2
CODE_REFUS = 3


class ClientRedis(Protocol):
    """Ce que la purge demande à un client Redis **synchrone** — et rien de plus.

    Un protocole plutôt que `redis.Redis` : c'est ce qui permet aux tests de
    substituer un client factice (comme le `docker` neutralisé du filet CI local)
    et de garder la purge sans Redis réel.
    """

    def ping(self) -> Any: ...

    def llen(self, name: str) -> Any: ...

    def hlen(self, name: str) -> Any: ...

    def hgetall(self, name: str) -> Any: ...

    def scan_iter(self, match: str | None = None, count: int | None = None) -> Any: ...

    def delete(self, *names: str) -> Any: ...


@dataclass(frozen=True)
class Perimetre:
    """Ce que la purge vise — les clés Redis et les dossiers, lus des constantes."""

    journal: str
    battements: str
    file_taches: str
    prefixe_boites: str
    diffusion: str
    conversations: Path
    ingestion: Path
    projets: Path


@dataclass(frozen=True)
class Inventaire:
    """Les comptes — ce qui partirait (`--check`) ou ce qui est parti (réel)."""

    evenements: int
    battements: int
    taches: int
    boites: int
    conversations: int
    televersements: int
    #: `None` quand `--projets` n'est pas demandé : les déclarations sont conservées.
    projets: int | None


def perimetre(settings: Settings | None = None) -> Perimetre:
    """Le périmètre de la purge, résolu comme l'API résout ses propres dépôts."""
    settings = settings or load_settings()
    return Perimetre(
        journal=CLE_JOURNAL_EVENEMENTS,
        battements=CLE_BATTEMENTS,
        file_taches=FILE_TACHES,
        prefixe_boites=CANAL_BOITE_PREFIXE,
        diffusion=CANAL_DIFFUSION,
        conversations=ChatStore.default(settings).racine,
        ingestion=racine_ingestion(settings),
        projets=ProjetStore.default(settings).racine,
    )


# ── Les deux refus ────────────────────────────────────────────────────────────


def port_api(environnement: dict[str, str] | None = None) -> int:
    """Le port de l'API à sonder : `MAESTRO_PORT_API`, sinon celui du CLI."""
    env = os.environ if environnement is None else environnement
    brut = (env.get(VARIABLE_PORT_API) or "").strip()
    return int(brut) if brut.isdigit() else PORT_DEFAUT


def api_repond(port: int, *, delai_s: float = DELAI_SONDE_S) -> bool:
    """Vrai si **quelque chose** répond en HTTP sur le port de l'API.

    Une réponse d'erreur compte comme une réponse : ce qu'on veut savoir est si
    un process sert encore ce port, pas s'il va bien.
    """
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/sante", timeout=delai_s):
            return True
    except HTTPError:
        return True
    except (URLError, OSError, ValueError):
        return False


def hotes_vivants(
    client: ClientRedis,
    cle: str = CLE_BATTEMENTS,
    *,
    maintenant: datetime | None = None,
) -> tuple[str, ...]:
    """Les runs dont l'hôte **bat encore** — ceux qui interdisent la purge.

    Un battement présent est celui d'un run non soldé (`solder_le_run` retire
    l'entrée en partant) : on le juge donc comme un run en cours. Un battement
    périmé est un orphelin, et un orphelin ne bloque rien — le ramasser est
    exactement ce que la purge est là pour faire.
    """
    bruts = client.hgetall(cle) or {}
    vivants = [
        _texte(run_id)
        for run_id, horodatage in bruts.items()
        if vitalite(EXECUTION_EN_COURS, _texte(horodatage), maintenant=maintenant)
        == VITALITE_VIVANT
    ]
    return tuple(sorted(vivants))


# ── Inventaire et purge ───────────────────────────────────────────────────────


def _cles_des_boites(client: ClientRedis, perimetre_: Perimetre) -> list[str]:
    """Les clés Redis des boîtes et de la diffusion — normalement aucune (pub/sub)."""
    cles = {_texte(cle) for cle in client.scan_iter(match=f"{perimetre_.prefixe_boites}*")}
    cles.update(_texte(cle) for cle in client.scan_iter(match=perimetre_.diffusion))
    return sorted(cles)


def _fichiers(dossier: Path) -> list[Path]:
    """Les fichiers d'un dossier de données, sa documentation versionnée exceptée."""
    if not dossier.is_dir():
        return []
    return sorted(
        chemin
        for chemin in dossier.rglob("*")
        if chemin.is_file() and not (chemin.parent == dossier and chemin.name in FICHIERS_CONSERVES)
    )


def _declarations(dossier: Path) -> list[Path]:
    """Les déclarations de projets : les `.json` à la racine du dépôt, et eux seuls."""
    if not dossier.is_dir():
        return []
    return sorted(chemin for chemin in dossier.glob("*.json") if chemin.is_file())


def inventaire(client: ClientRedis, perimetre_: Perimetre, *, projets: bool) -> Inventaire:
    """Compte ce que la purge retirerait — lecture seule, rien n'est écrit."""
    return Inventaire(
        evenements=int(client.llen(perimetre_.journal) or 0),
        battements=int(client.hlen(perimetre_.battements) or 0),
        taches=int(client.llen(perimetre_.file_taches) or 0),
        boites=len(_cles_des_boites(client, perimetre_)),
        conversations=len(_fichiers(perimetre_.conversations)),
        televersements=len(_fichiers(perimetre_.ingestion)),
        projets=len(_declarations(perimetre_.projets)) if projets else None,
    )


def _elaguer(dossier: Path) -> None:
    """Retire les sous-dossiers devenus vides — jamais le dossier lui-même."""
    if not dossier.is_dir():
        return
    for chemin in sorted(dossier.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if chemin.is_dir() and not any(chemin.iterdir()):
            chemin.rmdir()


def purger(client: ClientRedis, perimetre_: Perimetre, *, projets: bool) -> Inventaire:
    """Retire l'état d'exécution ; rend ce qui est parti, aux comptes d'`inventaire`."""
    compte = inventaire(client, perimetre_, projets=projets)
    cles = [perimetre_.journal, perimetre_.battements, perimetre_.file_taches]
    cles.extend(_cles_des_boites(client, perimetre_))
    client.delete(*cles)
    for dossier in (perimetre_.conversations, perimetre_.ingestion):
        for chemin in _fichiers(dossier):
            chemin.unlink()
        _elaguer(dossier)
    if projets:
        for chemin in _declarations(perimetre_.projets):
            chemin.unlink()
    return compte


# ── Le compte rendu ───────────────────────────────────────────────────────────


def _lignes(compte: Inventaire, perimetre_: Perimetre) -> list[str]:
    boites = f"{perimetre_.prefixe_boites}*, {perimetre_.diffusion}"
    postes = [
        ("journal des événements", perimetre_.journal, f"{compte.evenements} événement(s)"),
        ("battements de runs", perimetre_.battements, f"{compte.battements} run(s)"),
        ("file de tâches", perimetre_.file_taches, f"{compte.taches} tâche(s)"),
        ("boîtes et diffusion", boites, f"{compte.boites} clé(s)"),
        ("conversations", str(perimetre_.conversations), f"{compte.conversations} fichier(s)"),
        ("téléversements", str(perimetre_.ingestion), f"{compte.televersements} fichier(s)"),
        (
            "projets déclarés",
            str(perimetre_.projets),
            "conservés (--projets pour les retirer)"
            if compte.projets is None
            else f"{compte.projets} déclaration(s) — les dossiers de projet restent",
        ),
    ]
    largeur = max(len(nom) for nom, _, _ in postes)
    return [f"  {nom.ljust(largeur)}  {valeur}  ({support})" for nom, support, valeur in postes]


def _texte(valeur: Any) -> str:
    """Décode ce que rend le client Redis (octets par défaut) — jamais de levée."""
    return valeur.decode("utf-8", "replace") if isinstance(valeur, bytes) else str(valeur)


def _client_redis(settings: Settings) -> ClientRedis:
    # Import local : seule la purge réelle dépend du client.
    import redis

    client: ClientRedis = redis.Redis.from_url(settings.redis_url or REDIS_URL_DEFAUT)
    return client


def _refus(vivants: Sequence[str], api: bool, erreur: TextIO) -> int:
    if api:
        print(
            f"Purge refusée : l'API répond encore sur :{port_api()} — elle rejouerait et "
            f"servirait l'état qu'on vient de vider.\n  Arrêter d'abord : {GESTE_PREALABLE}",
            file=erreur,
        )
    else:
        print(
            "Purge refusée : un hôte détaché vit encore — il republierait dans le journal "
            f"vidé (run(s) {', '.join(vivants)}).\n  Solder d'abord : {GESTE_PREALABLE}",
            file=erreur,
        )
    return CODE_REFUS


def main(
    argv: Sequence[str] | None = None,
    *,
    client: ClientRedis | None = None,
    sonde_api: Callable[[], bool] | None = None,
    settings: Settings | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Point d'entrée : `--check` compte, sans lui la purge est faite. Voir l'en-tête du module.

    `client`, `sonde_api` et `settings` sont injectables **pour les tests** — un
    client factice, une sonde qui répond ce qu'on lui dit, des dossiers jetables.
    """
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    inconnus = [arg for arg in args if arg not in ("--check", "--projets")]
    if inconnus:
        print(f"{_USAGE}\n  argument(s) inconnu(s) : {' '.join(inconnus)}", file=erreur)
        return CODE_USAGE
    check = "--check" in args
    projets = "--projets" in args
    settings = settings or load_settings()

    if client is None:
        client = _client_redis(settings)
    try:
        client.ping()
    except Exception as exc:  # le client réel lève sa propre famille d'exceptions
        print(
            f"Redis injoignable ({exc}) — rien à purger sans le bus ; "
            f"le lancer : docker compose -f infra/docker-compose.yml up -d redis",
            file=erreur,
        )
        return CODE_REDIS_INJOIGNABLE

    api = (sonde_api or (lambda: api_repond(port_api())))()
    vivants = () if api else hotes_vivants(client, CLE_BATTEMENTS)
    perimetre_ = perimetre(settings)

    if check:
        compte = inventaire(client, perimetre_, projets=projets)
        print("Purge de l'état d'exécution — vérification, rien n'est écrit :", file=sortie)
        print("\n".join(_lignes(compte, perimetre_)), file=sortie)
        if api or vivants:
            return _refus(vivants, api, erreur)
        print("  la purge passerait (ni API vivante, ni hôte détaché en vol).", file=sortie)
        return CODE_FAIT

    if api or vivants:
        return _refus(vivants, api, erreur)
    compte = purger(client, perimetre_, projets=projets)
    print("Purge de l'état d'exécution — retiré :", file=sortie)
    print("\n".join(_lignes(compte, perimetre_)), file=sortie)
    return CODE_FAIT


if __name__ == "__main__":
    raise SystemExit(main())
