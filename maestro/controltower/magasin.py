"""Le magasin des événements de l'API : joignable ou non, et ce que l'API en dit (#1206).

La projection de la Control Tower se nourrit d'un **magasin** — le journal durable
qu'elle rejoue au démarrage (`persistence.EventLog`) et le bus dont sa pompe
consomme le flux. En mode serveur, les deux sont un Redis. Jusqu'à #1206, perdre
ce Redis ne se disait nulle part : l'API démarrait quand même (le rejeu échoué
n'allait qu'au journal technique), `/api/sante` répondait « ok », et chaque écran
lisait `200` et une liste vide — ou la dernière projection en mémoire, figée. Un
vide qui passe pour « aucune donnée » est un « ok » menteur, et il fausse toute
vérification qui s'appuie sur la vraie stack.

Ce module tient la réponse en un seul endroit :

- `Magasin` sait si le magasin répond — une **sonde** du journal lui-même
  (`EventLog.sonder`, un `PING` sur le client du journal), gardée quelques
  instants pour ne pas pinguer à chaque requête, et **une seule en vol** — et si
  le rejeu du démarrage a abouti ;
- `GardeMagasin` refuse en `503` motivé les requêtes de l'API tant que le
  magasin manque : le `detail` nomme la panne et le geste qui la lève, et c'est
  ce que l'écran affiche au lieu d'un vide ;
- `/api/sante` (dans `app`) rend l'état du magasin **dans son corps**, en
  `200` : la sonde dit « un process sert ce port » à la purge, au lanceur, à
  `start.sh` et au banc, qui l'attendent ainsi ; ce qui change est qu'elle ne
  répond plus « ok » quand ce n'est pas le cas.

L'API **reprend seule** : la pompe réessaie (`app._pompe`), rejoue le journal si
le démarrage n'avait pas pu, et la garde se lève dès que la sonde répond. Relancer
Redis suffit, et c'est le geste que la panne nomme.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from maestro.telemetry.redact import redact_secrets

#: Le geste exact quand le bus manque — l'instance déjà mutualisée avec la file
#: de tâches et les boîtes aux lettres (infra/docker-compose.yml). Le préflight
#: de `start.sh` (`cli --verifier-redis`, #186) et la panne de l'API (#1206) le
#: disent avec les mêmes mots.
COMMANDE_REDIS = "docker compose -f infra/docker-compose.yml up -d redis"

#: Délai de la sonde, en secondes. Un Redis éteint refuse la connexion tout de
#: suite ; un hôte qui ne répond plus (VPN coupé, relais tombé) ferait sinon
#: attendre chaque requête le time-out TCP du système.
DELAI_SONDE_S = 1.0

#: Durée pendant laquelle un verdict de la sonde tient, en secondes. Assez court
#: pour qu'un Redis relancé se voie au rechargement suivant, assez long pour
#: qu'un écran qui lit dix routes d'un coup ne pingue pas dix fois.
VALIDITE_SONDE_S = 1.0

#: Le nom de la panne dans un refus de la garde (`panne` du corps du 503) — ce
#: par quoi l'écran la reconnaît, sans lire le texte du `detail`.
PANNE_MAGASIN = "magasin"

#: Les routes que la garde laisse passer, **avec leur raison** — l'inventaire est
#: fermé exprès : une route qu'on ajoutera demain est gardée sans qu'on y pense.
ROUTES_HORS_GARDE: dict[str, str] = {
    "/api/sante": "elle dit la panne : la refuser la tairait",
    "/api/extinction": "s'arrêter doit rester possible, magasin ou non",
}


def endpoint_lisible(url: str) -> str:
    """L'URL Redis privée de ses identifiants — un diagnostic ne publie pas de mot de passe.

    `REDIS_URL` peut porter un `user:motdepasse@` (elle est d'ailleurs listée
    parmi les variables sensibles de `maestro.telemetry.redact`) ; le lanceur,
    lui, a besoin de dire **où** il a frappé. On ne garde donc que le point de
    connexion : schéma, hôte, port, base.
    """
    parties = urlsplit(url)
    hote = parties.hostname or ""
    if ":" in hote:  # IPv6 : les crochets font partie de l'écriture
        hote = f"[{hote}]"
    if parties.port:
        hote = f"{hote}:{parties.port}"
    return urlunsplit((parties.scheme, hote, parties.path, "", ""))


@dataclass(frozen=True)
class EtatMagasin:
    """Ce que l'API sait de son magasin, à l'instant de la question."""

    #: Le magasin répond **et** l'historique a été relu : l'état servi est complet.
    disponible: bool
    #: Où il vit (« Redis, redis://127.0.0.1:6379/0 »), `None` s'il vit en process.
    lieu: str | None = None
    #: Le nom de la panne, en tête (« Magasin des événements injoignable ») —
    #: `None` quand rien ne manque.
    titre: str | None = None
    #: Ce qu'on en sait et ce qu'elle coûte, en une phrase — `None` quand rien ne manque.
    motif: str | None = None
    #: Ce qu'il y a à faire, en mots — `None` quand rien ne manque.
    geste: str | None = None
    #: La commande exacte du geste, quand il en a une — séparée des mots pour que
    #: l'écran la montre comme une commande, et non noyée dans la phrase.
    commande: str | None = None

    def detail(self) -> str:
        """Le motif d'un refus, en une ligne lisible : la panne, où, pourquoi, puis le geste.

        C'est le `detail` que tout client lit ; l'écran, lui, compose la même
        chose à partir des champs (`en_json`), sans relire ce texte.
        """
        if self.titre is None:
            return ""
        texte = self.titre if self.lieu is None else f"{self.titre} ({self.lieu})"
        if self.motif:
            texte = f"{texte} : {self.motif}"
        if self.geste:
            texte = f"{texte} — {self.geste}"
            if self.commande:
                texte = f"{texte} ({self.commande})"
        return texte

    def en_json(self) -> dict[str, Any]:
        return {
            "disponible": self.disponible,
            "lieu": self.lieu,
            "titre": self.titre,
            "motif": self.motif,
            "geste": self.geste,
            "commande": self.commande,
        }


Sonde = Callable[[], Awaitable[None]]


class Magasin:
    """L'état du magasin des événements, tel que l'API le sert (#1206).

    Deux faits le composent, et il en faut les deux pour être disponible :

    - **la sonde répond** — le client du journal lui-même (`EventLog.sonder`) ;
    - **l'historique a été relu** — sinon l'état servi est celui d'une API neuve,
      et le montrer comme complet serait le même mensonge par un autre chemin.
      Le rejeu est retenté par la pompe dès que le magasin revient.
    """

    def __init__(
        self,
        sonde: Sonde,
        *,
        lieu: str | None = None,
        horloge: Callable[[], float] = time.monotonic,
        validite_s: float = VALIDITE_SONDE_S,
        delai_s: float = DELAI_SONDE_S,
    ) -> None:
        self._sonde = sonde
        self._lieu = lieu
        self._horloge = horloge
        self._validite_s = validite_s
        self._delai_s = delai_s
        self._verrou = asyncio.Lock()
        self._verdict: tuple[float, str | None] | None = None
        self._rejeu_fait = False
        self._echec_rejeu: str | None = None

    @property
    def rejeu_fait(self) -> bool:
        return self._rejeu_fait

    def rejeu_abouti(self) -> None:
        """L'historique durable a été relu : l'état servi est complet."""
        self._rejeu_fait = True
        self._echec_rejeu = None

    def rejeu_echoue(self, erreur: BaseException) -> None:
        """Le rejeu a échoué : la cause est retenue, c'est elle que la panne dira."""
        self._echec_rejeu = redact_secrets(str(erreur)) or type(erreur).__name__

    def oublier(self) -> None:
        """Oublie le dernier verdict : la question suivante sonde à nouveau.

        La pompe l'appelle quand elle perd le flux — elle sait avant la sonde
        que le magasin vient de tomber.
        """
        self._verdict = None

    async def etat(self) -> EtatMagasin:
        panne = await self._panne_sondee()
        if panne is not None:
            return EtatMagasin(
                disponible=False,
                lieu=self._lieu,
                titre="Magasin des événements injoignable",
                motif=(
                    f"{panne}. Rien de ce que l'écran montrerait n'est à jour, et ce "
                    "que publient les runs est perdu tant qu'il manque"
                ),
                geste="relancer Redis, l'API reprend seule",
                commande=COMMANDE_REDIS,
            )
        if not self._rejeu_fait:
            # Le magasin répond, mais l'historique n'est pas dans l'état servi :
            # soit il revient à l'instant (la pompe relit), soit sa relecture
            # échoue pour une autre raison — et c'est cette raison qu'on dit.
            if self._echec_rejeu is None:
                titre = "Historique pas encore relu"
                motif = "le magasin répond, l'API relit son journal"
            else:
                titre = "Historique illisible"
                motif = f"{self._echec_rejeu}. L'état servi serait celui d'une API neuve"
            return EtatMagasin(
                disponible=False,
                lieu=self._lieu,
                titre=titre,
                motif=motif,
                geste="l'API relit seule ; si la panne dure, voir le journal de maestro-api",
            )
        return EtatMagasin(disponible=True, lieu=self._lieu)

    async def _panne_sondee(self) -> str | None:
        """Le motif de la panne, ou `None` — le verdict tient `validite_s` secondes."""
        async with self._verrou:
            maintenant = self._horloge()
            if self._verdict is not None and maintenant - self._verdict[0] < self._validite_s:
                return self._verdict[1]
            panne: str | None
            try:
                await asyncio.wait_for(self._sonde(), timeout=self._delai_s)
                panne = None
            except TimeoutError:
                panne = f"pas de réponse en {self._delai_s:g} s"
            except Exception as erreur:  # noqa: BLE001 - tout échec vaut « injoignable »
                panne = redact_secrets(str(erreur)) or type(erreur).__name__
            self._verdict = (self._horloge(), panne)
            return panne


class GardeMagasin:
    """Refuse en `503` motivé ce que l'API servirait sans son magasin (#1206).

    ASGI pur, comme le portier d'accès (`acces.GardeAcces`) et pour la même
    raison : une route ajoutée demain est gardée sans qu'on l'écrive. Montée
    **sous** le portier — une requête sans jeton est refusée pour ce motif-là
    d'abord — et sous CORS, pour que la page puisse lire le `detail`.

    Le WebSocket passe : il ne sert que le flux à venir, et un client qui s'y
    branche lit l'état par le REST, où la panne l'attend.
    """

    def __init__(self, app: Any, magasin: Magasin) -> None:
        self._app = app
        self._magasin = magasin

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or not str(scope.get("path", "")).startswith("/api/")
            or scope.get("path") in ROUTES_HORS_GARDE
        ):
            await self._app(scope, receive, send)
            return
        etat = await self._magasin.etat()
        if etat.disponible:
            await self._app(scope, receive, send)
            return
        # `detail` pour tout client, et la panne **nommée** à côté : l'écran la
        # reconnaît par ce champ, jamais en relisant le texte (#996).
        corps = json.dumps(
            {"detail": etat.detail(), "panne": PANNE_MAGASIN, "magasin": etat.en_json()},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(corps)).encode("latin-1")),
                    # Un client qui réessaie seul sait quand : le verdict tient
                    # une seconde, la pompe réessaie à quelques secondes.
                    (b"retry-after", b"5"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": corps})
