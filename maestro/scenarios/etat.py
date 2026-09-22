"""L'état qu'un passage du banc a laissé : sauvé, rouvert, rejoué à la demande (#1164).

    .venv/Scripts/python.exe -m maestro.scenarios.etat --verifier [--rejouer | --neuf]
    .venv/Scripts/python.exe -m maestro.scenarios.etat --rouvrir
    .venv/Scripts/python.exe -m maestro.scenarios.etat --vider
    .venv/Scripts/python.exe -m maestro.scenarios.etat --decrire

Ce module n'est pas joué à la main : `scripts/controltower/start.sh --etat-banc`
l'appelle, et c'est ce geste-là qu'on retient (voir plus bas). Les captures des
présentations de jalon (`scripts/presentation/captures.sh`, #1166) l'appellent
aussi, pour tourner sur ce même état.

## Pourquoi

L'écran peuplé que regardent la relecture visuelle et les captures venait d'un
scénario **factice** (la démo). Arbitrage de #1156 : il vient désormais de
**l'état réel qu'un passage du banc (#1148) a laissé**, servi par l'API réelle.
Rien n'est fabriqué — un passage coûte du vrai modèle (~1 $ et ~40 min pour les
quatre scénarios, mesuré le 2026-09-22), on ne le rejoue donc pas à chaque
regard : on **rouvre** ce qu'il a laissé, et on dit son âge.

## Le jeu de données du banc

Un passage qui doit laisser un état se joue sur **le banc de la copie** — un
espace Redis et des dépôts de fichiers à lui (`maestro.controltower.donnees.
donnees_du_banc`), jamais ceux de la copie : l'état rouvert ne contient que ce
que le passage a fait, et le rouvrir n'écrase ni un worktree en cours de travail
ni les données du poste.

- **`--vider`** remet le banc à neuf avant un passage : aucun run, aucun fil,
  aucun projet ; la **configuration** de la copie (agents, autorisations,
  playbooks, serveurs MCP, capacités) recopiée sans ses rangements `_projets/`,
  qui appartiennent à des projets.
- Le banc joue (`python -m maestro.scenarios --sauver-etat`) contre l'API servie
  sur ce banc, puis **sauve** l'état : le journal durable de l'espace et chaque
  dépôt, sous `<atelier du passage>/_etat/` — à côté des projets jetables du
  passage, que les déclarations sauvées désignent. `--nettoyer` efface l'atelier,
  donc l'état : un passage nettoyé n'a rien laissé à rouvrir.
- **`--rouvrir`** remet le banc dans l'état du dernier passage sauvé : le journal
  réécrit dans l'espace du banc, les dépôts recopiés. L'API qui démarre ensuite
  rejoue ce journal comme après n'importe quel redémarrage — **rien n'est
  rejoué** du côté du modèle, et chaque ouverture repart du même état.
- **`--verifier`** est le préflight du lanceur : Redis joignable, rien en vol sur
  le banc, et — sauf `--rejouer` ou `--neuf` — un état à rouvrir, dont il **dit
  l'âge**.
- **`--decrire`** rend, en JSON sur une ligne, l'état que `--rouvrir` rouvrirait :
  passage, date, âge, verdicts, contenu. Il ne lit que le disque — ni Redis, ni le
  banc — et c'est ce qu'une présentation de jalon dit de ce qu'elle montre (#1166).

## Une stack neuve (#1165)

`start.sh --etat-neuf` sert le banc **remis à neuf, sans rien jouer** : aucun
run, aucun fil, aucun projet, la configuration de la copie. C'est l'état « vide »
que regarde la relecture visuelle — une stack réelle neuve, et non la démo qui
simulait l'absence. Le geste est celui qui précède un rejeu (`--vider`) ; seul
change qu'aucun passage ne suit, d'où `--verifier --neuf`, qui n'exige aucun état
sauvé et le dit autrement que `--rejouer`. Le banc vidé ne perd rien : l'état
d'un passage vit dans son atelier, et `--rouvrir` l'y reprend.

## Ce qu'il ne faut pas défaire

**Les battements ne sont pas sauvés** : ce sont des signaux de vie, pas de
l'état. Un run que le passage a laissé en vol se rouvre tel que l'API le juge
sans hôte — c'est ce que le produit ferait de ce run-là.

**On refuse tant que quelque chose vit sur le banc** — un hôte détaché qui bat,
ou une API qui sert l'espace du banc : ils republieraient dans le journal qu'on
réécrit. Même règle que la purge (#853), et même geste préalable.

Codes de sortie : `0` fait · `1` Redis injoignable · `2` usage · `3` refusé
(quelque chose vit sur le banc) · `4` aucun état du banc à rouvrir.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TextIO
from urllib.error import URLError
from urllib.request import urlopen

from maestro.agents.rangement import SEGMENT_PROJETS
from maestro.config import load_settings
from maestro.controltower.battement import CLE_BATTEMENTS
from maestro.controltower.donnees import (
    DEPOTS,
    Donnees,
    donnees_de_la_stack,
    donnees_du_banc,
    poser_sur_le_process,
)
from maestro.controltower.events import REDIS_URL_DEFAUT
from maestro.controltower.persistence import CLE_JOURNAL_EVENEMENTS
from maestro.controltower.purge import ClientRedis as ClientRedisPurge
from maestro.controltower.purge import hotes_vivants, port_api
from maestro.fichiers import retirer_arbre
from maestro.projets.modele import ID_PROJET
from maestro.queue.celery_app import FILE_TACHES
from maestro.scenarios.modele import Rapport
from maestro.scenarios.projets import racine_atelier

#: Le nom sous lequel ce module s'invoque — dérivé, jamais écrit (#830).
MODULE = __spec__.name if __spec__ is not None else __name__

#: Où l'état d'un passage se range, dans l'atelier du passage. Le tiret bas le met
#: hors d'atteinte d'un dossier de scénario (`s1-vider`…).
DOSSIER_ETAT = "_etat"
FICHIER_META = "etat.json"
FICHIER_JOURNAL = "journal.jsonl"
DOSSIER_DEPOTS = "depots"

#: La forme de `etat.json` — relevée si elle change, pour qu'un état ancien se
#: refuse au lieu de se rouvrir de travers.
VERSION_FORMAT = 1

#: Les gestes, nommés dans les annonces et les refus.
GESTE_ROUVRIR = "bash scripts/controltower/start.sh --etat-banc"
GESTE_REJOUER = "bash scripts/controltower/start.sh --etat-banc --rejouer"
GESTE_ARRETER = "bash scripts/controltower/start.sh --stop"

#: Le journal se réécrit par lots : un `RPUSH` par événement coûterait un aller
#: par événement, un seul `RPUSH` géant bloquerait l'instance partagée.
TAILLE_LOT = 500

CODE_FAIT = 0
CODE_REDIS_INJOIGNABLE = 1
CODE_USAGE = 2
CODE_REFUS = 3
CODE_AUCUN_ETAT = 4

_USAGE = (
    f"Usage : python -m {MODULE} --verifier [--rejouer | --neuf] | --rouvrir | --vider | --decrire"
)

#: Les gestes qu'accepte la ligne de commande — un seul par appel.
GESTES = ("--verifier", "--rouvrir", "--vider", "--decrire")

#: Ce qui accompagne `--verifier`, et lui seul — l'un ou l'autre.
MODES_VERIFIER = ("--rejouer", "--neuf")


class ClientRedis(ClientRedisPurge, Protocol):
    """Ce que ce module demande à un client Redis **synchrone** : celui de la purge,
    dont il partage le refus (`hotes_vivants`), plus de quoi lire et réécrire un journal."""

    def lrange(self, name: str, start: int, end: int) -> Any: ...

    def rpush(self, name: str, *values: str) -> Any: ...


# ── L'état sauvé ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Instantane:
    """L'état qu'un passage a laissé, tel qu'il est rangé sur le disque."""

    dossier: Path
    passage: str
    sauve_le: datetime
    evenements: int
    projets: int
    #: `(identifiant, verdict)` de chaque scénario joué, dans l'ordre du passage.
    scenarios: tuple[tuple[str, str], ...]
    duree_s: float
    cout_usd: float | None

    @classmethod
    def lire(cls, dossier: Path) -> Instantane | None:
        """L'état rangé dans `dossier`, ou None s'il manque, est illisible ou d'une autre forme."""
        try:
            meta = json.loads((dossier / FICHIER_META).read_text(encoding="utf-8"))
            if meta.get("version") != VERSION_FORMAT:
                return None
            cout = meta.get("cout_usd")
            return cls(
                dossier=dossier,
                passage=str(meta["passage"]),
                sauve_le=datetime.fromisoformat(meta["sauve_le"]),
                evenements=int(meta["evenements"]),
                projets=int(meta["projets"]),
                scenarios=tuple((str(s["id"]), str(s["verdict"])) for s in meta["scenarios"]),
                duree_s=float(meta.get("duree_s") or 0.0),
                cout_usd=None if cout is None else float(cout),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def age_s(self, maintenant: datetime | None = None) -> float:
        """L'âge de l'état, en secondes — jamais négatif (une horloge qui recule)."""
        maintenant = maintenant or datetime.now(UTC)
        return max(0.0, (maintenant - self.sauve_le).total_seconds())


def cles_du_banc(donnees: Donnees) -> tuple[str, str, str]:
    """Le journal, les battements et la file de l'espace — ce qu'un état remplace."""
    espace = donnees.espace
    return (
        espace.nommer(CLE_JOURNAL_EVENEMENTS),
        espace.nommer(CLE_BATTEMENTS),
        espace.nommer(FILE_TACHES),
    )


def _texte(valeur: Any) -> str:
    return valeur.decode("utf-8") if isinstance(valeur, bytes) else str(valeur)


def _compter_projets(racine: Path) -> int:
    if not racine.is_dir():
        return 0
    return sum(1 for chemin in racine.glob("*.json") if ID_PROJET.match(chemin.stem))


def sauver(
    rapport: Rapport,
    atelier: Path,
    donnees: Donnees,
    client: ClientRedis,
    *,
    maintenant: datetime | None = None,
) -> Instantane:
    """Range l'état de la stack `donnees` sous `<atelier>/_etat/` et le rend.

    Écrit à côté puis renommé : un passage coupé pendant qu'on sauve laisse
    l'état précédent intact, jamais un état à moitié écrit qu'on rouvrirait.
    """
    partiel = atelier / f"{DOSSIER_ETAT}.partiel"
    retirer_arbre(partiel)
    partiel.mkdir(parents=True)

    journal = cles_du_banc(donnees)[0]
    bruts = client.lrange(journal, 0, -1) or []
    # Une chaîne JSON par ligne : un événement ne peut pas casser le découpage,
    # quoi qu'il contienne.
    lignes = [json.dumps(_texte(brut), ensure_ascii=False) for brut in bruts]
    (partiel / FICHIER_JOURNAL).write_text(
        "".join(f"{ligne}\n" for ligne in lignes), encoding="utf-8"
    )

    for depot in DEPOTS:
        source = donnees.racines[depot.nom]
        if source.is_dir():
            shutil.copytree(source, partiel / DOSSIER_DEPOTS / depot.nom)

    meta = {
        "version": VERSION_FORMAT,
        "passage": rapport.horodatage,
        "sauve_le": (maintenant or datetime.now(UTC)).isoformat(timespec="seconds"),
        "espace": donnees.espace.nom,
        "evenements": len(lignes),
        "projets": _compter_projets(partiel / DOSSIER_DEPOTS / "projets"),
        "scenarios": [
            {"id": r.identifiant, "verdict": r.verdict, "run_id": r.run_id}
            for r in rapport.resultats
        ],
        "duree_s": rapport.duree_s,
        "cout_usd": rapport.cout_usd,
    }
    (partiel / FICHIER_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    final = atelier / DOSSIER_ETAT
    retirer_arbre(final)
    partiel.rename(final)
    instantane = Instantane.lire(final)
    if instantane is None:  # pragma: no cover - on vient de l'écrire
        raise OSError(f"état écrit mais illisible : {final}")
    return instantane


def dernier(racine: Path | None = None) -> Instantane | None:
    """Le dernier état sauvé parmi tous les passages du poste, ou None."""
    racine = racine or racine_atelier()
    if not racine.is_dir():
        return None
    etats = [
        etat
        for passage in racine.iterdir()
        if (etat := Instantane.lire(passage / DOSSIER_ETAT)) is not None
    ]
    return max(etats, key=lambda e: e.sauve_le, default=None)


# ── Le banc : vider, rouvrir ──────────────────────────────────────────────────


def vider(banc: Donnees, copie: Donnees, client: ClientRedis) -> None:
    """Remet le banc à neuf : ni run, ni fil, ni projet ; la configuration de la copie."""
    client.delete(*cles_du_banc(banc))
    for depot in DEPOTS:
        cible = banc.racines[depot.nom]
        retirer_arbre(cible)
        source = copie.racines[depot.nom]
        if not depot.etat and source.is_dir():
            shutil.copytree(source, cible, ignore=shutil.ignore_patterns(SEGMENT_PROJETS))
        else:
            cible.mkdir(parents=True, exist_ok=True)


def rouvrir(instantane: Instantane, banc: Donnees, client: ClientRedis) -> None:
    """Remet le banc dans l'état sauvé : journal réécrit, dépôts recopiés."""
    journal = cles_du_banc(banc)[0]
    client.delete(*cles_du_banc(banc))
    texte = (instantane.dossier / FICHIER_JOURNAL).read_text(encoding="utf-8")
    evenements = [json.loads(ligne) for ligne in texte.splitlines() if ligne.strip()]
    for debut in range(0, len(evenements), TAILLE_LOT):
        client.rpush(journal, *evenements[debut : debut + TAILLE_LOT])
    for depot in DEPOTS:
        cible = banc.racines[depot.nom]
        retirer_arbre(cible)
        source = instantane.dossier / DOSSIER_DEPOTS / depot.nom
        if source.is_dir():
            shutil.copytree(source, cible)
        else:
            cible.mkdir(parents=True, exist_ok=True)


# ── Ce qui se dit ─────────────────────────────────────────────────────────────


def age_lisible(secondes: float) -> str:
    """« il y a 3 h 05 min » — l'âge d'un état, à la précision qui compte pour le juger."""
    s = int(secondes)
    if s < 60:
        return "à l'instant" if s < 5 else f"il y a {s} s"
    minutes, heures, jours = s // 60, s // 3600, s // 86400
    if heures == 0:
        return f"il y a {minutes} min"
    if jours == 0:
        return f"il y a {heures} h {minutes % 60:02d} min"
    return f"il y a {jours} j {heures % 24} h"


def _duree_lisible(secondes: float) -> str:
    minutes = round(secondes / 60)
    return f"{minutes} min" if minutes else f"{round(secondes)} s"


def annonce(instantane: Instantane, *, maintenant: datetime | None = None) -> list[str]:
    """Ce que dit la réouverture : quel passage, **quel âge**, ce qu'il contient, et le rejeu."""
    sauve = instantane.sauve_le.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    scenarios = " · ".join(f"{i} {v}" for i, v in instantane.scenarios) or "aucun"
    prix = _duree_lisible(instantane.duree_s)
    if instantane.cout_usd is not None:
        prix += f", {instantane.cout_usd:.2f} $".replace(".", ",")
    return [
        f"État du banc — passage {instantane.passage}, sauvé "
        f"{age_lisible(instantane.age_s(maintenant))} ({sauve})",
        f"  scénarios  {scenarios}",
        f"  contenu    {instantane.evenements} événement(s) · {instantane.projets} projet(s)",
        f"  rejouer    {GESTE_REJOUER}  (vrai modèle — ce passage : {prix})",
    ]


def description(instantane: Instantane, *, maintenant: datetime | None = None) -> dict[str, Any]:
    """L'état à rouvrir, tel qu'un autre outil le relit — ce que `annonce` dit en prose."""
    return {
        "passage": instantane.passage,
        "sauve_le": instantane.sauve_le.isoformat(timespec="seconds"),
        "age_s": round(instantane.age_s(maintenant)),
        "scenarios": [{"id": i, "verdict": v} for i, v in instantane.scenarios],
        "evenements": instantane.evenements,
        "projets": instantane.projets,
    }


# ── Les refus ────────────────────────────────────────────────────────────────


def api_du_banc_en_marche(banc: Donnees, port: int) -> bool:
    """Vrai si une API sert **l'espace du banc** sur ce port — elle rejouerait l'ancien état."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/sante", timeout=2.0) as reponse:
            corps = json.loads(reponse.read().decode("utf-8") or "{}")
    except (URLError, OSError, ValueError):
        return False
    return isinstance(corps, dict) and corps.get("espace") == banc.espace.nom


def _refus_vivant(
    banc: Donnees, client: ClientRedis, sonde_api: Callable[[], bool], erreur: TextIO
) -> bool:
    """Dit et rend True si quelque chose vit sur le banc (voir l'en-tête)."""
    if sonde_api():
        print(
            f"Refusé : une API sert encore le banc (espace « {banc.espace.nom} ») — elle "
            f"garderait l'ancien état en mémoire.\n  L'arrêter d'abord : {GESTE_ARRETER}",
            file=erreur,
        )
        return True
    vivants = hotes_vivants(client, cles_du_banc(banc)[1])
    if vivants:
        print(
            f"Refusé : des runs du banc sont encore en vol ({', '.join(vivants)}) — ils "
            f"republieraient dans le journal réécrit.\n  Les solder d'abord : {GESTE_ARRETER}",
            file=erreur,
        )
        return True
    return False


def _aucun_etat(erreur: TextIO) -> int:
    print(
        f"Aucun état du banc à rouvrir sous {racine_atelier()} — aucun passage n'en a "
        f"sauvé (ou son atelier a été nettoyé).\n  En jouer un : {GESTE_REJOUER}",
        file=erreur,
    )
    return CODE_AUCUN_ETAT


# ── La ligne de commande ─────────────────────────────────────────────────────


def client_redis() -> ClientRedis:
    """Le client Redis synchrone de la configuration — celui du banc et de ce module."""
    import redis

    client: ClientRedis = redis.Redis.from_url(load_settings().redis_url or REDIS_URL_DEFAUT)
    return client


def _verifier_redis_du_banc() -> int:
    """Le préflight Redis de l'API, sur le banc : ping, puis l'annonce de ses données."""
    from maestro.controltower.cli import verifier_redis

    return verifier_redis()


def main(
    argv: Sequence[str] | None = None,
    *,
    client: ClientRedis | None = None,
    verifier_redis: Callable[[], int] | None = None,
    sonde_api: Callable[[], bool] | None = None,
    copie: Path | None = None,
    racine_ateliers: Path | None = None,
    maintenant: datetime | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Point d'entrée : voir l'en-tête du module. Les paramètres nommés servent les tests."""
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    gestes = [a for a in args if a in GESTES]
    modes = [a for a in args if a in MODES_VERIFIER]
    reste = [a for a in args if a not in (*GESTES, *MODES_VERIFIER)]
    if len(gestes) != 1 or reste or len(modes) > 1 or (modes and gestes != ["--verifier"]):
        print(_USAGE, file=erreur)
        return CODE_USAGE
    geste = gestes[0]
    rejouer = modes == ["--rejouer"]
    neuf = modes == ["--neuf"]

    # Décrire ne lit que le disque : ni Redis ni le banc n'ont à répondre pour dire
    # ce qu'on rouvrirait.
    if geste == "--decrire":
        instantane = dernier(racine_ateliers)
        if instantane is None:
            return _aucun_etat(erreur)
        # ASCII échappé : la ligne est faite pour être redirigée vers un fichier, et un
        # tube Windows l'écrirait sinon en cp1252 (#141).
        print(json.dumps(description(instantane, maintenant=maintenant)), file=sortie)
        return CODE_FAIT

    # La copie d'abord, le banc ensuite : une fois le process placé sur le banc,
    # « les données de la stack » seraient celles du banc.
    donnees_copie = donnees_de_la_stack()
    banc = donnees_du_banc(racine=copie)

    if geste == "--verifier":
        poser_sur_le_process(banc)
        code = (verifier_redis or _verifier_redis_du_banc)()
        if code != CODE_FAIT:
            return CODE_REDIS_INJOIGNABLE
    client = client or client_redis()
    try:
        client.ping()
    except Exception as exc:  # le client réel lève sa propre famille d'exceptions
        print(f"Redis injoignable ({exc}) — le banc vit sur Redis.", file=erreur)
        return CODE_REDIS_INJOIGNABLE

    sonde = sonde_api or (lambda: api_du_banc_en_marche(banc, port_api()))
    if _refus_vivant(banc, client, sonde, erreur):
        return CODE_REFUS

    if geste == "--vider" or (geste == "--verifier" and (rejouer or neuf)):
        if geste == "--vider":
            vider(banc, donnees_copie, client)
            print(
                "Banc remis à neuf : aucun run, aucun fil, aucun projet — la configuration "
                "de la copie, sans ses rangements de projets.",
                file=sortie,
            )
        elif neuf:
            print(
                "Une stack neuve : le banc repart à neuf et se sert tel quel, sans rien "
                "jouer ni rouvrir — aucun run, aucun fil, aucun projet.",
                file=sortie,
            )
        else:
            print(
                "Le banc repart à neuf et rejoue ses scénarios contre la vraie stack "
                "(vrai modèle) ; son état sera sauvé à la fin du passage.",
                file=sortie,
            )
        return CODE_FAIT

    instantane = dernier(racine_ateliers)
    if instantane is None:
        return _aucun_etat(erreur)
    if geste == "--rouvrir":
        rouvrir(instantane, banc, client)
        print(
            f"Banc rouvert sur le passage {instantane.passage} — "
            f"{instantane.evenements} événement(s), {instantane.projets} projet(s), "
            "rien n'est rejoué.",
            file=sortie,
        )
    else:
        print("\n".join(annonce(instantane, maintenant=maintenant)), file=sortie)
    return CODE_FAIT


if __name__ == "__main__":
    raise SystemExit(main())
