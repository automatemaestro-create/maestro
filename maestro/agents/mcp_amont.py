"""Client du **registre MCP officiel** et son miroir local (#675, lot 1/6 du parent #673).

La bibliothèque MCP de la Control Tower est aujourd'hui un **seed en code**
(`SEED` dans `maestro.agents.mcp_registry`, 29 entrées écrites à la main). Le
parent #673 lui ajoute une seconde source — le registre officiel
(`registry.modelcontextprotocol.io`, porté par Anthropic, GitHub, Microsoft et
PulseMCP). Ce module en est le **socle** : parler à l'amont, et **n'en dépendre
à aucun instant**.

**Pourquoi un miroir, et pas un appel direct.** Le registre est en préversion et
annonce lui-même « does not provide uptime or data durability guarantees » ; sa
doc d'*aggregator* demande de moissonner « on a regular but infrequent basis
(e.g. once per hour) » et de **persister chez soi**. Mesuré le 2026-08-28 :
25 333 serveurs, `limit` plafonné à **100** (422 au-delà), pagination par
`cursor` opaque de la forme `<nom>:<version>`, ~1,3 s l'aller — soit **254 pages
et ~10 minutes** pour un moissonnage complet. Un miroir local n'est donc pas une
optimisation de latence : c'est la seule façon d'adosser un écran à cette source.

## Les trois questions que ce module tranche

1. **Que redemande-t-on ?** Le premier passage moissonne tout
   (`version=latest`, `limit=100`, `cursor`) ; les suivants ne demandent que
   `updated_since=<borne>` (avec `include_deleted`, que l'amont force à `true`
   dans ce mode — mesuré : sur 100 entrées d'une fenêtre incrémentale, 96
   `active`, 1 `deprecated`, **3 `deleted`**, sans avoir passé le paramètre).
   On le passe quand même : une requête doit énoncer ce qu'elle obtient.

2. **D'où vient la borne ?** De l'**horloge de l'amont**, jamais de la nôtre, et
   c'est l'en-tête `Date` de la **première page** — pas le `max(updatedAt)` vu.
   Les deux se discutent, et le `max` est faux : la pagination parcourt les noms
   dans l'ordre **alphabétique**, donc une entrée en début d'alphabet modifiée
   pendant qu'on lit la fin ne sera pas vue, alors que son `updatedAt` reste
   *sous* le maximum de la passe — elle serait manquée **pour toujours**. Le
   début de la passe, lui, borne ce qu'on est sûr d'avoir vu (moins une seconde,
   `Date` n'ayant que la résolution de la seconde). Notre horloge est écartée
   pour une raison plus simple : décalée en avant elle **saute** des entrées, en
   silence. `max(updatedAt)` reste le repli quand l'en-tête est illisible, et à
   défaut la borne ne bouge pas — le passage suivant remoissonne tout, ce qui
   coûte dix minutes mais ne perd rien.

3. **Que fait-on d'un amont fâché ?** Rien qui remonte à l'écran.
   `MiroirAmont.rafraichir` **ne lève jamais** : il rend un `Rafraichissement`
   qui porte `ok`, la `cause` et les compteurs, laisse le miroir précédent
   **intact** sur le disque, et écrit la cause dans l'état pour qu'un écran
   ouvert trois heures plus tard puisse la dire. Les trois familles de panne du
   critère d'acceptation ont chacune leur exception interne — `AmontInjoignable`
   (réseau, DNS, TLS), `AmontTropLent` (délai d'un aller, ou budget de la passe)
   et `AmontHorsContrat` (statut HTTP, JSON illisible, enveloppe inattendue,
   pagination qui boucle) — parce qu'« injoignable » et « répond n'importe quoi »
   n'appellent pas le même geste.

## Ce que le miroir garde, et ce qu'il n'invente pas

Le document `server.json` est stocké **verbatim** (`EntreeAmont.document`) :
seuls les champs dont le rafraîchissement a besoin (nom, version, statut, dates)
sont extraits à côté. Un miroir qui remodèle sa source est un miroir qu'il faut
remoissonner — dix minutes — chaque fois que le lecteur change d'avis ; et le
schéma amont est **en préversion** (`2025-12-11`, et `2025-09-29` sur les
entrées anciennes), donc il bougera. La traduction `server.json` → entrée de
bibliothèque est le lot 2 (#676) et vit ailleurs.

Le `status` est le **seul champ mutable** de l'amont, et la doc de modération
demande de le tenir à jour : une entrée passée `deprecated` **reste** dans le
miroir avec son statut (elle est signalée, pas cachée), une entrée `deleted`
en **sort**. Un statut inconnu — la préversion peut en ajouter — est conservé
tel quel et l'entrée reste visible : seul `deleted` retire.

Le miroir est une **donnée**, pas du code versionné : il vit sous
`core/mcp-amont/` (remplaçable par `MAESTRO_MCP_AMONT_DIR`) et n'est pas commité
— voir `core/mcp-amont/README.md`. Deux fichiers, écrits atomiquement et **dans
cet ordre** : `miroir.jsonl` (une entrée par ligne) puis `etat.json`. L'ordre est
le contenu de la décision — une coupure entre les deux laisse une borne en
retard, donc un passage suivant qui redemande un peu trop, ce qui est idempotent ;
l'ordre inverse laisserait une borne en avance sur des données jamais écrites,
c'est-à-dire un trou définitif.

Tests différés → lot 6 du parent (#680).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx2

from maestro.config import Settings, load_settings

_LOG = logging.getLogger(__name__)

#: L'amont par défaut — le registre MCP officiel (`MAESTRO_MCP_AMONT_URL`).
AMONT_DEFAUT = "https://registry.modelcontextprotocol.io"

#: La route de listing du registre (`GET /v0.1/servers`).
CHEMIN_SERVEURS = "/v0.1/servers"

#: Le plafond de pagination de l'amont — **mesuré** : au-delà il répond 422.
LIMITE_PAGE = 100

#: Le délai d'un aller. Un aller coûte ~1,3 s en régime nominal ; 30 s laisse de
#: la marge à un amont lent sans immobiliser un rafraîchissement de fond.
TIMEOUT_S = 30.0

#: Le budget de la passe entière. Un moissonnage complet coûte ~602 s mesurées ;
#: 1800 s laissent un facteur trois avant qu'on déclare l'amont trop lent —
#: au-delà, la passe est abandonnée et le miroir précédent sert tel quel.
BUDGET_S = 1800.0

#: La périodicité par défaut du rafraîchissement (≥ 1 h, comme la doc d'amont le
#: demande) — un écran ne moissonne pas, il lit le miroir.
PERIODE_DEFAUT_S = 3600

#: Le plancher de la périodicité : un réglage à `0` voudrait dire « moissonner à
#: chaque requête d'écran », c'est-à-dire dix minutes d'amont par affichage.
#: On borne au lieu de refuser — le réglage garde son sens, pas sa dérive.
PERIODE_MINIMALE_S = 60

#: La clé d'extension que l'amont pose sur chaque entrée : `status`,
#: `statusChangedAt`, `publishedAt`, `updatedAt`, `isLatest`.
CLE_META_OFFICIELLE = "io.modelcontextprotocol.registry/official"

STATUT_ACTIF = "active"
STATUT_DEPRECIE = "deprecated"
STATUT_SUPPRIME = "deleted"

MODE_COMPLET = "complet"
MODE_INCREMENTAL = "incremental"

#: La marge retranchée à l'en-tête `Date` de la première page : elle n'a que la
#: résolution de la seconde, donc reculer d'une seconde garantit qu'on ne saute
#: rien (au prix d'une poignée d'entrées redemandées, ce qui est idempotent).
MARGE_BORNE_S = 1

#: Ce qu'on cite d'une réponse fâchée dans la cause — assez pour diagnostiquer,
#: pas assez pour déverser une page d'erreur dans un état persisté.
_EXTRAIT = 300


class ErreurAmont(RuntimeError):
    """L'amont n'a pas rendu ce qu'il fallait — **jamais remontée à l'écran**.

    Interne au module : `MiroirAmont.rafraichir` l'attrape et la mue en cause
    lisible dans son compte rendu. Trois familles, parce que « injoignable »,
    « trop lent » et « répond hors contrat » n'appellent pas le même geste.
    """


class AmontInjoignable(ErreurAmont):
    """Réseau, DNS, TLS, connexion refusée : l'amont n'a pas répondu du tout."""


class AmontTropLent(ErreurAmont):
    """Délai d'un aller dépassé, ou budget de la passe entière épuisé."""


class AmontHorsContrat(ErreurAmont):
    """Statut HTTP, JSON, enveloppe ou pagination inattendus : il répond, mal."""


@dataclass(frozen=True)
class EntreeAmont:
    """Une entrée du registre officiel telle que le miroir la garde.

    `document` est le `server.json` **verbatim** — c'est lui que le lot 2 (#676)
    traduira en entrée de bibliothèque. Les autres champs en sont extraits pour
    ce que le rafraîchissement doit savoir sans rouvrir le document : l'identité
    (`nom`, qui est la clé du miroir), le `statut` mutable et les dates dont
    `mis_a_jour_le` sert de repli à la borne incrémentale.
    """

    nom: str
    version: str = ""
    description: str = ""
    statut: str = STATUT_ACTIF
    statut_change_le: str = ""
    publie_le: str = ""
    mis_a_jour_le: str = ""
    est_derniere: bool = True
    document: dict[str, Any] = field(default_factory=dict)

    @property
    def supprimee(self) -> bool:
        """L'entrée est `deleted` : elle **sort** du miroir (modération amont)."""
        return self.statut == STATUT_SUPPRIME

    @property
    def obsolete(self) -> bool:
        """L'entrée est `deprecated` : elle reste visible, et **signalée**."""
        return self.statut == STATUT_DEPRECIE

    @classmethod
    def depuis_amont(cls, brut: object) -> EntreeAmont:
        """Lit une entrée de l'enveloppe amont (`{"server": …, "_meta": …}`).

        Strict sur `server.name` — sans identité, une entrée n'est ni stockable
        ni fusionnable : elle est refusée (`AmontHorsContrat`), comptée parmi les
        ignorées, et la passe continue. Tolérant sur `_meta`, qui est une
        **extension** : disparue, l'entrée reste mirroir-able en `active` sans
        dates, et c'est la borne qui en pâtira (repli sur un moissonnage complet)
        plutôt que la donnée.
        """
        if not isinstance(brut, dict):
            raise AmontHorsContrat(f"entrée qui n'est pas un objet JSON ({type(brut).__name__}).")
        serveur = brut.get("server")
        if not isinstance(serveur, dict):
            raise AmontHorsContrat("entrée sans objet « server ».")
        nom = _texte(serveur.get("name")).strip()
        if not nom:
            raise AmontHorsContrat("entrée sans « server.name » exploitable.")
        meta = brut.get("_meta")
        brut_officiel = meta.get(CLE_META_OFFICIELLE) if isinstance(meta, dict) else None
        officiel: dict[str, Any] = brut_officiel if isinstance(brut_officiel, dict) else {}
        return cls(
            nom=nom,
            version=_texte(serveur.get("version")),
            description=_texte(serveur.get("description")),
            statut=_texte(officiel.get("status")) or STATUT_ACTIF,
            statut_change_le=_texte(officiel.get("statusChangedAt")),
            publie_le=_texte(officiel.get("publishedAt")),
            mis_a_jour_le=_texte(officiel.get("updatedAt")),
            est_derniere=bool(officiel.get("isLatest", True)),
            document=serveur,
        )

    @classmethod
    def depuis_miroir(cls, brut: dict[str, Any]) -> EntreeAmont:
        """Relit une entrée écrite par ce module (une ligne de `miroir.jsonl`)."""
        document = brut.get("document")
        return cls(
            nom=_texte(brut.get("nom")),
            version=_texte(brut.get("version")),
            description=_texte(brut.get("description")),
            statut=_texte(brut.get("statut")) or STATUT_ACTIF,
            statut_change_le=_texte(brut.get("statut_change_le")),
            publie_le=_texte(brut.get("publie_le")),
            mis_a_jour_le=_texte(brut.get("mis_a_jour_le")),
            est_derniere=bool(brut.get("est_derniere", True)),
            document=document if isinstance(document, dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'entrée en dict JSON-sérialisable (forme du miroir et de l'API)."""
        return {
            "nom": self.nom,
            "version": self.version,
            "description": self.description,
            "statut": self.statut,
            "statut_change_le": self.statut_change_le,
            "publie_le": self.publie_le,
            "mis_a_jour_le": self.mis_a_jour_le,
            "est_derniere": self.est_derniere,
            "document": self.document,
        }


@dataclass(frozen=True)
class EtatMiroir:
    """Ce que le miroir sait de lui-même — persisté dans `etat.json`.

    `rafraichi_le` et `moissonne_le` sont sur **notre** horloge (ce sont des
    dates d'exploitation : « le miroir est-il périmé ? ») ; `borne_amont` est sur
    celle de l'**amont** (c'est un filigrane qu'on lui renvoie tel quel en
    `updated_since`). Les mélanger est précisément l'erreur que le module évite.

    `cause`/`echoue_le` portent le **dernier échec**, et sont vidés dès qu'un
    passage réussit : c'est ce qui permet à un écran ouvert trois heures après la
    panne de dire pourquoi le miroir n'a pas bougé, plutôt que d'afficher une
    fraîcheur qu'il ne peut pas justifier.
    """

    amont: str = ""
    rafraichi_le: str = ""
    moissonne_le: str = ""
    borne_amont: str = ""
    nombre: int = 0
    cause: str = ""
    echoue_le: str = ""

    @classmethod
    def from_dict(cls, brut: dict[str, Any]) -> EtatMiroir:
        """Relit l'état depuis `etat.json` — tolérant, un champ absent vaut vide."""
        nombre = brut.get("nombre")
        return cls(
            amont=_texte(brut.get("amont")),
            rafraichi_le=_texte(brut.get("rafraichi_le")),
            moissonne_le=_texte(brut.get("moissonne_le")),
            borne_amont=_texte(brut.get("borne_amont")),
            nombre=nombre if isinstance(nombre, int) and not isinstance(nombre, bool) else 0,
            cause=_texte(brut.get("cause")),
            echoue_le=_texte(brut.get("echoue_le")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'état en dict JSON-sérialisable (forme publique API/UI)."""
        return {
            "amont": self.amont,
            "rafraichi_le": self.rafraichi_le,
            "moissonne_le": self.moissonne_le,
            "borne_amont": self.borne_amont,
            "nombre": self.nombre,
            "cause": self.cause,
            "echoue_le": self.echoue_le,
        }


@dataclass(frozen=True)
class Moisson:
    """Ce qu'une passe a rapporté de l'amont — avant toute fusion au miroir.

    `vues` compte les entrées **reçues**, `ignorees` celles que l'amont a servies
    sans identité exploitable : les deux ensemble disent si un moissonnage vide
    est un amont calme ou un schéma qui a bougé sous nos pieds.
    """

    entrees: tuple[EntreeAmont, ...] = ()
    pages: int = 0
    vues: int = 0
    ignorees: int = 0
    horloge_amont: str = ""


@dataclass(frozen=True)
class Rafraichissement:
    """Le compte rendu d'un passage — **rendu, jamais levé** (critère d'acceptation).

    `ok` faux veut dire « le miroir précédent sert tel quel » et jamais « le
    miroir est vide » : sur échec, rien n'est écrit côté données. `cause` nomme
    la famille de panne et son détail ; elle est aussi persistée dans l'état.
    """

    ok: bool
    mode: str
    cause: str = ""
    pages: int = 0
    vues: int = 0
    ajoutees: int = 0
    majs: int = 0
    retirees: int = 0
    ignorees: int = 0
    duree_s: float = 0.0
    etat: EtatMiroir = field(default_factory=EtatMiroir)

    def resume(self) -> str:
        """Une ligne lisible — ce qu'on journalise et ce qu'un écran peut citer."""
        if not self.ok:
            return f"miroir MCP non rafraîchi ({self.mode}) — {self.cause}"
        return (
            f"miroir MCP rafraîchi ({self.mode}) : {self.etat.nombre} entrée(s), "
            f"+{self.ajoutees} ~{self.majs} -{self.retirees}"
            f"{f' · {self.ignorees} ignorée(s)' if self.ignorees else ''} "
            f"en {self.duree_s:.1f} s ({self.pages} page(s))"
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet le compte rendu en dict JSON-sérialisable."""
        return {
            "ok": self.ok,
            "mode": self.mode,
            "cause": self.cause,
            "pages": self.pages,
            "vues": self.vues,
            "ajoutees": self.ajoutees,
            "majs": self.majs,
            "retirees": self.retirees,
            "ignorees": self.ignorees,
            "duree_s": round(self.duree_s, 3),
            "etat": self.etat.to_dict(),
        }


class ClientRegistreOfficiel:
    """Lecture du registre officiel : pagination par curseur, en un seul verbe.

    `moissonner()` sans borne fait le **moissonnage complet** (`version=latest`,
    `limit=100`, curseur suivi jusqu'à épuisement) ; avec `depuis=<RFC 3339>` il
    fait l'**incrémental** (`updated_since`, `include_deleted=true`). Il ne
    connaît pas le miroir : il rend une `Moisson`, et c'est `MiroirAmont` qui
    décide quoi en faire.

    Un `client` httpx injecté (tests, ou réutilisation d'un pool) est utilisé tel
    quel et **jamais fermé** par ce module : on ne ferme que ce qu'on a ouvert.
    """

    def __init__(
        self,
        *,
        amont: str = AMONT_DEFAUT,
        timeout_s: float = TIMEOUT_S,
        budget_s: float = BUDGET_S,
        client: httpx2.Client | None = None,
    ) -> None:
        self.amont = amont.rstrip("/") or AMONT_DEFAUT
        self._timeout_s = timeout_s
        self._budget_s = budget_s
        self._client = client
        self._a_nous = client is None

    def __enter__(self) -> ClientRegistreOfficiel:
        return self

    def __exit__(self, *_: object) -> None:
        self.fermer()

    def fermer(self) -> None:
        """Ferme le client HTTP **si ce module l'a ouvert** (idempotent)."""
        if self._client is not None and self._a_nous:
            self._client.close()
            self._client = None

    def moissonner(self, *, depuis: str = "") -> Moisson:
        """Toutes les pages de `GET /v0.1/servers`, du curseur vide à l'épuisement.

        `depuis` vide → moissonnage complet ; sinon rafraîchissement incrémental
        borné par `updated_since`. Lève une `ErreurAmont` — c'est l'appelant
        (`MiroirAmont`) qui en fait une cause, jamais une exception à l'écran.
        """
        params: dict[str, str] = {"version": "latest", "limit": str(LIMITE_PAGE)}
        if depuis:
            params["updated_since"] = depuis
            # L'amont force déjà `include_deleted` à true dans ce mode (mesuré) ;
            # on le passe quand même — une requête doit énoncer ce qu'elle obtient.
            params["include_deleted"] = "true"

        entrees: list[EntreeAmont] = []
        pages = vues = ignorees = 0
        horloge = ""
        curseur = ""
        deja_vus: set[str] = set()
        debut = perf_counter()

        while True:
            ecoule = perf_counter() - debut
            if ecoule > self._budget_s:
                raise AmontTropLent(
                    f"passe abandonnée après {ecoule:.0f} s et {pages} page(s) "
                    f"(budget {self._budget_s:.0f} s) — l'amont ne suit pas."
                )
            requete = dict(params)
            if curseur:
                requete["cursor"] = curseur
            charge, date_http = self._page(requete)
            pages += 1
            if not horloge:
                horloge = date_http

            servers = charge.get("servers")
            if not isinstance(servers, list):
                raise AmontHorsContrat(
                    "enveloppe sans liste « servers » "
                    f"(page {pages}, reçu {type(servers).__name__})."
                )
            for entree_brute in servers:
                vues += 1
                try:
                    entrees.append(EntreeAmont.depuis_amont(entree_brute))
                except AmontHorsContrat:
                    ignorees += 1

            metadata = charge.get("metadata")
            suivant = metadata.get("nextCursor") if isinstance(metadata, dict) else None
            if not isinstance(suivant, str) or not suivant:
                break
            if suivant in deja_vus:
                raise AmontHorsContrat(
                    f"pagination qui boucle : curseur {suivant!r} déjà servi "
                    f"(page {pages})."
                )
            deja_vus.add(suivant)
            curseur = suivant

        if vues and not entrees:
            raise AmontHorsContrat(
                f"{vues} entrée(s) reçue(s), aucune exploitable — le schéma amont "
                "a changé (aucune n'a de « server.name »)."
            )
        return Moisson(
            entrees=tuple(entrees),
            pages=pages,
            vues=vues,
            ignorees=ignorees,
            horloge_amont=horloge,
        )

    def _page(self, params: dict[str, str]) -> tuple[dict[str, Any], str]:
        """Un aller : la charge JSON de la page, et l'horloge de l'amont (`Date`)."""
        url = f"{self.amont}{CHEMIN_SERVEURS}"
        if self._client is None:
            self._client = httpx2.Client(timeout=self._timeout_s, follow_redirects=True)
        try:
            reponse = self._client.get(url, params=params)
        except httpx2.TimeoutException as exc:
            raise AmontTropLent(f"{url} : délai dépassé ({exc}).") from exc
        except httpx2.HTTPError as exc:
            raise AmontInjoignable(f"{url} injoignable : {exc}") from exc
        if reponse.status_code != 200:
            raise AmontHorsContrat(
                f"{url} a répondu {reponse.status_code} : {reponse.text[:_EXTRAIT]}"
            )
        try:
            charge: object = reponse.json()
        except ValueError as exc:
            raise AmontHorsContrat(
                f"{url} n'a pas répondu en JSON : {reponse.text[:_EXTRAIT]}"
            ) from exc
        if not isinstance(charge, dict):
            raise AmontHorsContrat(
                f"{url} : enveloppe inattendue ({type(charge).__name__}, objet attendu)."
            )
        return charge, _horloge_http(reponse.headers.get("date", ""))


class MiroirAmont:
    """Le miroir local du registre officiel — la source dont l'écran dépend.

    Deux fichiers sous `racine` : `miroir.jsonl` (une entrée par ligne, triées
    par nom) et `etat.json` (fraîcheur, borne incrémentale, dernière cause
    d'échec). `entrees()` et `etat` lisent ; `rafraichir()` est le **seul**
    écrivain, et il ne lève jamais.
    """

    FICHIER_ENTREES = "miroir.jsonl"
    FICHIER_ETAT = "etat.json"

    def __init__(
        self,
        racine: Path,
        *,
        amont: str = AMONT_DEFAUT,
        periode_s: int = PERIODE_DEFAUT_S,
    ) -> None:
        self._racine = racine
        self._amont = amont.rstrip("/") or AMONT_DEFAUT
        self._periode_s = max(int(periode_s), PERIODE_MINIMALE_S)
        self._memo: tuple[tuple[int, int], tuple[EntreeAmont, ...]] | None = None

    @classmethod
    def default(cls, settings: Settings | None = None) -> MiroirAmont:
        """Le miroir configuré : `MAESTRO_MCP_AMONT_*`, sinon `core/mcp-amont/`."""
        settings = settings or load_settings()
        racine = (
            Path(settings.mcp_amont_dir)
            if settings.mcp_amont_dir
            else Path(__file__).resolve().parents[2] / "core" / "mcp-amont"
        )
        return cls(
            racine,
            amont=settings.mcp_amont_url or AMONT_DEFAUT,
            periode_s=_entier(settings.mcp_amont_periode, PERIODE_DEFAUT_S),
        )

    @property
    def racine(self) -> Path:
        """La racine du miroir (deux fichiers : les entrées et l'état)."""
        return self._racine

    @property
    def amont(self) -> str:
        """Le registre moissonné — dit à l'écran, jamais deviné."""
        return self._amont

    @property
    def periode_s(self) -> int:
        """La périodicité effective du rafraîchissement, plancher appliqué."""
        return self._periode_s

    @property
    def etat(self) -> EtatMiroir:
        """L'état du miroir ; un état absent ou illisible vaut « jamais moissonné ».

        Le repli va vers le **plus prudent** : sans borne lisible, le passage
        suivant refait un moissonnage complet — dix minutes, jamais un trou.
        """
        chemin = self._racine / self.FICHIER_ETAT
        try:
            brut: object = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return EtatMiroir(amont=self._amont)
        if not isinstance(brut, dict):
            return EtatMiroir(amont=self._amont)
        return EtatMiroir.from_dict(brut)

    def entrees(self) -> tuple[EntreeAmont, ...]:
        """Les entrées du miroir, triées par nom — aucune `deleted` par construction.

        Mémoïsé sur l'empreinte du fichier (mtime + taille) : la bibliothèque et
        l'écran relisent à chaque requête, et le miroir pèse plusieurs dizaines
        de milliers de lignes. L'empreinte, et non une durée : une écriture
        change les deux, donc le cache tombe à l'instant même où il ment.
        """
        chemin = self._racine / self.FICHIER_ENTREES
        try:
            marque = chemin.stat()
        except OSError:
            self._memo = None
            return ()
        empreinte = (marque.st_mtime_ns, marque.st_size)
        if self._memo is not None and self._memo[0] == empreinte:
            return self._memo[1]
        entrees: list[EntreeAmont] = []
        try:
            with chemin.open(encoding="utf-8") as flux:
                for ligne in flux:
                    texte = ligne.strip()
                    if not texte:
                        continue
                    try:
                        brut: object = json.loads(texte)
                    except ValueError:
                        continue
                    if isinstance(brut, dict):
                        entree = EntreeAmont.depuis_miroir(brut)
                        if entree.nom:
                            entrees.append(entree)
        except OSError:
            self._memo = None
            return ()
        lot = tuple(sorted(entrees, key=lambda entree: entree.nom))
        self._memo = (empreinte, lot)
        return lot

    def perime(self, maintenant: datetime | None = None) -> bool:
        """Le miroir a-t-il dépassé sa périodicité ? (jamais rafraîchi → oui)."""
        instant = _instant(self.etat.rafraichi_le)
        if instant is None:
            return True
        reference = maintenant or datetime.now(UTC)
        return (reference - instant).total_seconds() >= self._periode_s

    def rafraichir_si_perime(
        self,
        client: ClientRegistreOfficiel | None = None,
        *,
        maintenant: datetime | None = None,
    ) -> Rafraichissement | None:
        """Rafraîchit **seulement** si la périodicité est dépassée ; sinon None.

        C'est le verbe qu'appelle un lecteur (bibliothèque, écran) : il ne
        moissonne pas à chaque requête, il constate d'abord.
        """
        if not self.perime(maintenant):
            return None
        return self.rafraichir(client, maintenant=maintenant)

    def rafraichir(
        self,
        client: ClientRegistreOfficiel | None = None,
        *,
        complet: bool = False,
        maintenant: datetime | None = None,
    ) -> Rafraichissement:
        """Un passage sur l'amont, fusionné dans le miroir — **ne lève jamais**.

        Sans borne connue (ou `complet=True`), c'est un moissonnage complet qui
        **remplace** le miroir ; sinon un incrémental qui le **fusionne** par nom.
        Dans les deux cas les `deleted` sortent et les `deprecated` restent.

        Sur n'importe quelle panne — amont injoignable, trop lent, hors contrat,
        ou disque qui refuse l'écriture —, le miroir précédent reste **intact** et
        la cause est rendue *et* persistée. Un moissonnage complet qui viderait un
        miroir non vide est traité comme une panne : c'est le cas que le critère
        d'acceptation nomme (« jamais un miroir vidé »), et il ne se distingue pas
        d'un amont en panne par la seule lecture de sa réponse.
        """
        debut = perf_counter()
        etat = self.etat
        borne = "" if complet else etat.borne_amont
        mode = MODE_COMPLET if not borne else MODE_INCREMENTAL
        a_nous = client is None
        client = client or ClientRegistreOfficiel(amont=self._amont)
        try:
            moisson = client.moissonner(depuis=borne)
        except ErreurAmont as exc:
            return self._echec(mode, f"{_famille(exc)} — {exc}", debut, maintenant)
        finally:
            if a_nous:
                client.fermer()

        avant = {entree.nom: entree for entree in self.entrees()}
        apres = dict(avant) if mode == MODE_INCREMENTAL else {}
        for entree in moisson.entrees:
            if entree.supprimee:
                apres.pop(entree.nom, None)
                continue
            apres[entree.nom] = entree

        if mode == MODE_COMPLET and avant and not apres:
            return self._echec(
                mode,
                "amont hors contrat — un moissonnage complet sans aucune entrée "
                f"viderait un miroir de {len(avant)} entrée(s) : il est conservé.",
                debut,
                maintenant,
            )

        noms_avant, noms_apres = set(avant), set(apres)
        instant = maintenant or datetime.now(UTC)
        horodatage = _iso(instant)
        etat_neuf = EtatMiroir(
            amont=self._amont,
            rafraichi_le=horodatage,
            moissonne_le=horodatage if mode == MODE_COMPLET else etat.moissonne_le,
            borne_amont=_plus_recente(
                etat.borne_amont,
                moisson.horloge_amont or _borne_des(moisson.entrees),
            ),
            nombre=len(apres),
        )
        try:
            self._ecrire(apres, etat_neuf)
        except OSError as exc:
            return self._echec(mode, f"miroir non écrit — {exc}", debut, maintenant)

        compte_rendu = Rafraichissement(
            ok=True,
            mode=mode,
            pages=moisson.pages,
            vues=moisson.vues,
            ajoutees=len(noms_apres - noms_avant),
            majs=sum(1 for nom in noms_apres & noms_avant if apres[nom] != avant[nom]),
            retirees=len(noms_avant - noms_apres),
            ignorees=moisson.ignorees,
            duree_s=perf_counter() - debut,
            etat=etat_neuf,
        )
        _LOG.info("%s", compte_rendu.resume())
        return compte_rendu

    def _echec(
        self,
        mode: str,
        cause: str,
        debut: float,
        maintenant: datetime | None,
    ) -> Rafraichissement:
        """Consigne la cause **sans toucher aux données** et rend le compte rendu."""
        etat = replace(
            self.etat,
            amont=self._amont,
            cause=cause,
            echoue_le=_iso(maintenant or datetime.now(UTC)),
        )
        try:
            self._ecrire_etat(etat)
        except OSError:
            # Dire la cause ne doit pas produire une seconde panne : le compte
            # rendu la porte de toute façon, et le miroir n'a pas bougé.
            pass
        compte_rendu = Rafraichissement(
            ok=False,
            mode=mode,
            cause=cause,
            duree_s=perf_counter() - debut,
            etat=etat,
        )
        _LOG.warning("%s", compte_rendu.resume())
        return compte_rendu

    def _ecrire(self, entrees: dict[str, EntreeAmont], etat: EtatMiroir) -> None:
        """Écrit les entrées **puis** l'état, chacun atomiquement (voir l'en-tête)."""
        self._racine.mkdir(parents=True, exist_ok=True)
        chemin = self._racine / self.FICHIER_ENTREES
        temporaire = chemin.parent / f"{chemin.name}.tmp"
        with temporaire.open("w", encoding="utf-8", newline="\n") as flux:
            for nom in sorted(entrees):
                flux.write(json.dumps(entrees[nom].to_dict(), ensure_ascii=False) + "\n")
        os.replace(temporaire, chemin)
        self._memo = None
        self._ecrire_etat(etat)

    def _ecrire_etat(self, etat: EtatMiroir) -> None:
        """Remplace `etat.json` atomiquement (tampon puis renommage)."""
        self._racine.mkdir(parents=True, exist_ok=True)
        chemin = self._racine / self.FICHIER_ETAT
        temporaire = chemin.parent / f"{chemin.name}.tmp"
        temporaire.write_text(
            json.dumps(etat.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)


def _texte(valeur: object) -> str:
    """`valeur` si c'est une chaîne, sinon la chaîne vide — jamais un `str(dict)`."""
    return valeur if isinstance(valeur, str) else ""


def _entier(valeur: str | None, defaut: int) -> int:
    """Un réglage entier venu de l'environnement, `defaut` s'il est illisible."""
    try:
        return int((valeur or "").strip())
    except ValueError:
        return defaut


def _iso(instant: datetime) -> str:
    """L'instant en RFC 3339 UTC suffixé `Z` — la forme que l'amont sert et lit."""
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _instant(iso: str) -> datetime | None:
    """Un horodatage RFC 3339 en `datetime` aware, ou None s'il est illisible."""
    if not iso:
        return None
    try:
        lu = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return lu if lu.tzinfo is not None else lu.replace(tzinfo=UTC)


def _horloge_http(entete: str) -> str:
    """L'en-tête `Date` d'une réponse en borne RFC 3339, moins `MARGE_BORNE_S`.

    C'est l'horloge de l'**amont** : la seule qui ne puisse pas être décalée par
    rapport aux `updatedAt` qu'on lui redemandera. Vide si l'en-tête manque ou
    ne se lit pas — l'appelant se rabat alors sur le maximum des `updatedAt`.
    """
    if not entete:
        return ""
    try:
        instant = parsedate_to_datetime(entete)
    except (TypeError, ValueError):
        return ""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return _iso(instant - timedelta(seconds=MARGE_BORNE_S))


def _borne_des(entrees: tuple[EntreeAmont, ...]) -> str:
    """Le plus grand `updatedAt` d'une moisson — le **repli** de la borne.

    Repli seulement : il peut manquer une entrée modifiée pendant la passe, en
    amont du curseur (voir l'en-tête du module). On le préfère malgré tout à
    « pas de borne du tout », qui coûterait un moissonnage complet par passage.
    """
    meilleure = ""
    for entree in entrees:
        candidate = entree.mis_a_jour_le or entree.publie_le
        meilleure = _plus_recente(meilleure, candidate)
    return meilleure


def _plus_recente(actuelle: str, candidate: str) -> str:
    """La plus récente des deux bornes — **jamais** un recul, jamais un trou.

    Comparaison sur l'instant parsé et non sur la chaîne : l'amont sert
    aujourd'hui un format uniforme, mais une borne qui reculerait parce que deux
    formats se comparent mal ferait remoissonner en boucle, et une borne qui
    avancerait à tort ferait sauter des entrées en silence.
    """
    if not candidate:
        return actuelle
    if not actuelle:
        return candidate
    gauche, droite = _instant(actuelle), _instant(candidate)
    if gauche is None:
        return candidate
    if droite is None:
        return actuelle
    return candidate if droite > gauche else actuelle


def _famille(exc: ErreurAmont) -> str:
    """Le nom de la famille de panne, en tête de la cause — le geste en dépend."""
    if isinstance(exc, AmontTropLent):
        return "amont trop lent"
    if isinstance(exc, AmontInjoignable):
        return "amont injoignable"
    return "amont hors contrat"
