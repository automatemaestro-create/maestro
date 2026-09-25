"""La porte d'entrée réelle du banc : l'API de la Control Tower, en HTTP (#1148).

Le banc **n'importe pas** l'application. Il parle à l'API qui tourne, par les
appels exacts que l'écran envoie — `POST /api/chat/orchestrateur/messages`,
l'accord, le suivi du run. C'est le point du ticket : ce qu'un utilisateur fait
passe par le réseau, par le fil, par le vrai fournisseur ; une `TestClient`
monterait une pile *ressemblante*, et c'est précisément ce qui n'a rien vu du
défaut de #1146 pendant dix jours.

Deux couches, et la frontière entre elles est ce qui rend le banc testable sans
réseau :

- `Transport` — « envoie cette requête, rends ce statut et ce corps », et rien de
  plus. `TransportHTTP` en est l'implémentation réelle (`httpx2`, déjà au socle) ;
  les tests en substituent une fausse API qui répond ce qu'on lui dit ;
- `ClientAPI` — les **verbes du banc** au-dessus d'un transport : déclarer un
  projet, ouvrir une conversation, envoyer un message, valider l'équipe, donner
  l'accord, lire un run. C'est ici que vivent les chemins et les formes de corps,
  une seule fois, et c'est tout ce que les scénarios connaissent de l'API.

## Deux façons de recevoir une réponse du fil (#1265)

`envoyer` lit la paire que rend `POST …/messages` : **ce qui** a été répondu,
jamais **comment** c'est arrivé. C'est suffisant pour tout oracle qui porte sur
une réponse ; ce ne l'est pas pour le direct (#1222), qui est une propriété de
l'arrivée elle-même. `envoyer_en_direct` emprunte donc la route de l'écran
(`POST …/flux`, `apps/web/lib/useChat.ts`) et rend un `Echange` : chaque trame
SSE, datée **à sa réception** par le transport. Le produit sert les deux routes
par le même échange (`ServiceChat.envoyer`/`diffuser`) : ce qui change entre elles
est le rendu, jamais la réponse.

⚠ **Aucun nom de canal n'est recopié.** Le fil de l'orchestration s'adresse par
`NOM_ORCHESTRATION`, le port par `port_api` de la purge, les types de trame par
les `FRAGMENT_CHAT_*` du canal : la leçon de #830 est qu'une constante recopiée
de l'autre côté d'une frontière finit par désigner autre chose sans que rien ne
le dise.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from maestro.controltower.acces import entetes_client
from maestro.controltower.chat import (
    FRAGMENT_CHAT_DELTA,
    FRAGMENT_CHAT_ERREUR,
    FRAGMENT_CHAT_ETAPE,
    FRAGMENT_CHAT_FIN,
    FRAGMENT_CHAT_INTERROMPU,
)
from maestro.controltower.orchestration import NOM_ORCHESTRATION
from maestro.controltower.purge import port_api
from maestro.controltower.state import (
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    STATUTS_EXECUTION_TERMINAUX,
    VALIDATION_EN_ATTENTE,
)

#: Le chemin du fil de l'orchestrateur — la seule porte de lancement qu'un écran
#: offre depuis #666, donc la seule que le banc a le droit d'emprunter.
FIL = f"/api/chat/{NOM_ORCHESTRATION}"

#: Délai d'une requête HTTP **ordinaire** : une réponse que l'API rend sans le
#: modèle — déclarer, lire, créer, trancher. Court à dessein : une API figée doit
#: arrêter le scénario vite, et le dire. Le suivi d'un run, lui, ne dépend pas de
#: ce délai : il repose sur des lectures courtes répétées (`attendre_le_run`).
DELAI_REQUETE_S = 30.0

#: Le délai laissé à un run pour se solder. Quarante minutes : un run réel décompose,
#: exécute et agrège avec le vrai modèle, et ce modèle est depuis #1270 la dernière
#: Opus pour chaque agent, plus lent et plus soigneux que Sonnet. Quinze minutes
#: suffisaient sur Sonnet ; sur Opus 5.5, S6 (un Designer recruté en cours de run,
#: qui dessine, anime puis vérifie son rendu) a été coupé à 18 min encore en vol, et
#: rejoué sans borne serrée il a abouti en 31 min 18 s (passage 20260924-185935).
#: Ce n'est pas un plafond de dépense — c'est la borne au-delà de laquelle le banc
#: cesse d'attendre et le dit (`--delai` la règle).
#:
#: C'est aussi la borne d'une requête dont **le modèle rédige la réponse**
#: (`ClientAPI`, #1232) : l'écran n'en impose aucune à ces routes, et le banc
#: n'invente pas pour elles un second chiffre — il leur accorde ce qu'il accorde
#: déjà au modèle pour un run entier.
DELAI_RUN_S = 2400.0

#: L'intervalle entre deux lectures d'un run en vol. Une seconde : le run dure
#: des minutes, et interroger plus souvent ne ferait que charger l'API qu'on
#: mesure.
INTERVALLE_SUIVI_S = 1.0

#: La durée d'une image d'écran, à 60 Hz — le grain auquel une personne **voit**
#: une réponse s'écrire (#1265). Deux incréments reçus dans la même image
#: s'affichent ensemble : pour qui regarde, ils n'en font qu'un. C'est ce qui
#: sépare le direct d'un bloc débité en rafale — un transport qui a tout tamponné
#: rend ses trames à quelques microsecondes d'écart, un modèle qui rédige les
#: espace de dizaines de millisecondes (seize incréments visibles en 3,1 s au
#: bouclage du 2026-09-24). Ce n'est pas un seuil choisi pour le banc : c'est
#: l'écran de la personne, au rafraîchissement le plus courant — et le plus
#: indulgent pour une rafale.
IMAGE_S = 1 / 60


class ErreurAPI(RuntimeError):
    """L'API a refusé, ou n'a pas répondu ce que le banc attendait.

    Porte le `statut` HTTP (0 quand rien n'a répondu) et le `chemin` visé : un
    scénario rouge doit pouvoir dire *où* il s'est arrêté, pas seulement qu'il
    s'est arrêté.
    """

    def __init__(self, message: str, *, statut: int = 0, chemin: str = "") -> None:
        super().__init__(message)
        self.statut = statut
        self.chemin = chemin


@dataclass(frozen=True)
class Reponse:
    """Ce qu'un transport rend : le statut, le corps décodé, le texte brut."""

    statut: int
    corps: Any = None
    texte: str = ""

    @property
    def ok(self) -> bool:
        """La requête a-t-elle abouti (2xx) ?"""
        return 200 <= self.statut < 300


@dataclass(frozen=True)
class Trame:
    """Une trame du flux d'une réponse, et l'instant où le banc l'a **reçue** (#1265).

    `donnees` est le `FragmentChat` tel que l'API l'a sérialisé (`data: <json>`),
    sans rien rejuger. `instant` est lu à la réception, sur l'horloge du
    transport : c'est ce que ni la paire de `POST …/messages` ni le fil relu ne
    donnent, et c'est tout ce qui distingue une réponse écrite sous les yeux d'une
    réponse posée d'un coup.
    """

    instant: float
    donnees: Mapping[str, Any]

    @property
    def type(self) -> str:
        """Le type de la trame — un des `FRAGMENT_CHAT_*` du canal."""
        return str(self.donnees.get("type") or "")


@dataclass(frozen=True)
class Echange:
    """Une réponse reçue **par le flux**, trame par trame — ce que l'écran voit venir (#1265).

    `envoi` est l'instant où la requête est partie, sur la même horloge que les
    trames : l'attente se mesure de là. `statut` et `texte` portent un refus —
    un 422 part avant la première trame, en statut HTTP comme sur toute route.

    Les mesures sont **structurelles** et ne lisent aucun mot (#746) : des trames
    `fragment` qui portent du texte, des trames `etape`, et leurs instants.
    """

    statut: int
    envoi: float = 0.0
    trames: tuple[Trame, ...] = ()
    texte: str = ""

    @property
    def ok(self) -> bool:
        """Le flux s'est-il ouvert (2xx) ?"""
        return 200 <= self.statut < 300

    @property
    def cloture(self) -> Trame | None:
        """La dernière trame — celle qui dit comment l'échange s'est terminé."""
        return self.trames[-1] if self.trames else None

    @property
    def reponse(self) -> dict[str, Any]:
        """Le message de l'agent, tel que la trame `fin` le porte — `{}` sans elle.

        C'est le message **persisté** (`ServiceChat._repondre`) : le même que la
        seconde moitié de la paire de `POST …/messages`.
        """
        cloture = self.cloture
        if cloture is None or cloture.type != FRAGMENT_CHAT_FIN:
            return {}
        message = cloture.donnees.get("message")
        return dict(message) if isinstance(message, Mapping) else {}

    @property
    def increments(self) -> tuple[Trame, ...]:
        """Les trames qui **ajoutent du texte** à l'écran — un `fragment` vide n'en ajoute pas."""
        return tuple(
            trame
            for trame in self.trames
            if trame.type == FRAGMENT_CHAT_DELTA and str(trame.donnees.get("delta") or "")
        )

    @property
    def etapes(self) -> tuple[dict[str, str], ...]:
        """Les étapes publiées en direct (#1223) — celles qui portent un libellé.

        Une étape sans libellé n'aurait rien à afficher, et l'écran la saute
        (`etapes_depuis` fait de même au rechargement) : la compter dirait « il a
        lu » d'une lecture que personne n'a vue.
        """
        vues: list[dict[str, str]] = []
        for trame in self.trames:
            etape = trame.donnees.get("etape")
            if trame.type != FRAGMENT_CHAT_ETAPE or not isinstance(etape, Mapping):
                continue
            libelle = str(etape.get("libelle") or "")
            if libelle:
                vues.append({"libelle": libelle, "detail": str(etape.get("detail") or "")})
        return tuple(vues)

    @property
    def images(self) -> int:
        """En combien d'images d'écran le texte de la réponse est apparu — 0 sans incrément.

        Chaque incrément tombe dans l'image que son instant désigne, comptée depuis
        le **premier** : ancrer là, et non sur une horloge absolue, fait qu'une
        rafale de quelques microsecondes ne chevauche jamais deux images par
        hasard. Le rang est arrondi à la microseconde d'image avant d'être tronqué :
        en flottants, « une image plus tard » vaut 0,999… image, et retomberait
        dans la précédente.
        """
        instants = [trame.instant for trame in self.increments]
        if not instants:
            return 0
        premier = instants[0]
        return len({math.floor(round((i - premier) / IMAGE_S, 6)) for i in instants})

    @property
    def attente_s(self) -> float | None:
        """De l'envoi au premier incrément — ce que couvre l'indicateur d'attente.

        `None` quand aucun texte n'est venu : il n'y a pas eu de fin d'attente.
        """
        increments = self.increments
        return increments[0].instant - self.envoi if increments else None

    @property
    def ecriture_s(self) -> float:
        """Du premier incrément au dernier — le temps pendant lequel le texte s'écrit."""
        increments = self.increments
        return increments[-1].instant - increments[0].instant if increments else 0.0


class Transport(Protocol):
    """Ce que le banc demande à un transport HTTP — et rien de plus.

    Un protocole plutôt qu'un client concret, pour la raison qui a fait celui de
    `maestro.controltower.purge` : c'est ce qui permet aux tests de jouer le
    déroulé entier contre une fausse API, sans réseau ni serveur.

    Deux gestes : une requête dont on attend la réponse entière (`demander`), et
    une requête dont on **écoute** la réponse venir (`flux`, #1265). Le second
    n'est pas un détail du premier : c'est le transport qui date chaque trame, et
    une date prise après coup ne dirait plus rien du direct.
    """

    def demander(
        self,
        methode: str,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        delai_s: float | None = None,
    ) -> Reponse:
        """Joue la requête — `delai_s` la borne, `None` laissant le délai du transport."""
        ...

    def flux(
        self,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        delai_s: float | None = None,
    ) -> Echange:
        """Poste `corps` sur une route SSE et rend l'échange, chaque trame datée à sa réception."""
        ...


class TransportHTTP:
    """Le transport réel : `httpx2` sur la boucle locale, en synchrone.

    Synchrone à dessein : le banc est un déroulé, pas un serveur — il fait une
    chose après l'autre et attend chaque fois de savoir ce qui s'est passé.

    Il porte le **jeton de l'API locale** (#638) sur chaque requête, résolu par
    `maestro.controltower.acces` : le banc frappe la vraie API, qui sert durcie,
    et l'en-tête se pose là où sont déjà l'URL et le délai — jamais recopié dans
    un scénario. Vide en régime ouvert.
    """

    def __init__(self, base: str, *, delai_s: float = DELAI_REQUETE_S) -> None:
        self._base = base.rstrip("/")
        self._delai_s = delai_s
        self._entetes = entetes_client()

    @property
    def base(self) -> str:
        """L'adresse de l'API que ce transport vise."""
        return self._base

    def demander(
        self,
        methode: str,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        delai_s: float | None = None,
    ) -> Reponse:
        """Joue la requête et rend sa réponse — une panne réseau devient `ErreurAPI`.

        Un délai dépassé **n'est pas** une API injoignable, et le message les
        sépare (#1232) : « API injoignable (timed out) » a fait chercher une
        panne de l'API là où elle répondait, mais plus lentement que le banc ne
        l'attendait.
        """
        import httpx2

        delai = self._delai_s if delai_s is None else delai_s
        try:
            with httpx2.Client(timeout=delai) as client:
                brute = client.request(
                    methode,
                    f"{self._base}{chemin}",
                    json=None if corps is None else dict(corps),
                    params=dict(params or {}),
                    headers=dict(self._entetes),
                )
        except httpx2.TimeoutException as echec:
            raise ErreurAPI(
                f"l'API n'a pas répondu en {delai:g} s ({methode} {chemin})",
                chemin=chemin,
            ) from echec
        except httpx2.HTTPError as echec:  # pragma: no cover - API éteinte, réseau coupé
            raise ErreurAPI(f"API injoignable ({echec})", chemin=chemin) from echec
        texte = brute.text
        try:
            corps_decode = json.loads(texte) if texte else None
        except ValueError:
            corps_decode = None
        return Reponse(statut=brute.status_code, corps=corps_decode, texte=texte)

    def flux(
        self,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        delai_s: float | None = None,
    ) -> Echange:
        """Poste sur une route SSE et **écoute** la réponse venir (#1265).

        Le corps est lu ligne à ligne pendant qu'il arrive, et chaque événement
        est daté quand sa ligne vide le clôt — l'instant où un `EventSource` le
        remettrait à l'écran. `time.perf_counter` et non `time.monotonic` : sous
        Windows, le second avance par pas de ~16 ms, c'est-à-dire d'une image
        entière, et ne séparerait plus une rafale d'un direct.

        `delai_s` borne l'attente **entre deux lectures**, pas l'échange entier :
        c'est le silence d'une API figée qu'on veut arrêter, jamais une réponse
        longue qui continue de s'écrire.
        """
        import httpx2

        delai = self._delai_s if delai_s is None else delai_s
        trames: list[Trame] = []
        donnees: list[str] = []
        envoi = time.perf_counter()
        try:
            with (
                httpx2.Client(timeout=delai) as client,
                client.stream(
                    "POST",
                    f"{self._base}{chemin}",
                    json=None if corps is None else dict(corps),
                    headers={**dict(self._entetes), "Accept": "text/event-stream"},
                ) as brute,
            ):
                if not 200 <= brute.status_code < 300:
                    brute.read()
                    return Echange(statut=brute.status_code, envoi=envoi, texte=brute.text)
                for ligne in brute.iter_lines():
                    if ligne.startswith("data:"):
                        donnees.append(ligne[len("data:") :].removeprefix(" "))
                        continue
                    if ligne.strip() or not donnees:
                        continue
                    trame = _trame(time.perf_counter(), donnees)
                    donnees = []
                    if trame is not None:
                        trames.append(trame)
                statut = brute.status_code
        except httpx2.TimeoutException as echec:
            raise ErreurAPI(
                f"l'API n'a plus rien envoyé en {delai:g} s (POST {chemin})", chemin=chemin
            ) from echec
        except httpx2.HTTPError as echec:  # pragma: no cover - API éteinte, flux coupé
            raise ErreurAPI(f"flux interrompu ({echec})", chemin=chemin) from echec
        # Un flux clos sans ligne vide finale laisse son dernier événement en suspens.
        derniere = _trame(time.perf_counter(), donnees) if donnees else None
        if derniere is not None:
            trames.append(derniere)
        return Echange(statut=statut, envoi=envoi, trames=tuple(trames))


def _trame(instant: float, donnees: Sequence[str]) -> Trame | None:
    """L'événement SSE fait de ces lignes `data:` — `None` s'il n'est pas un objet JSON.

    Une trame illisible est **sautée** : elle ne dirait rien au banc, et la
    compter fausserait le décompte des incréments. Les trames du canal sont des
    objets (`FragmentChat.to_dict`) ; tout le reste est du bruit de transport.
    """
    try:
        objet = json.loads("\n".join(donnees))
    except ValueError:
        return None
    return Trame(instant=instant, donnees=objet) if isinstance(objet, Mapping) else None


def base_locale(environnement: Mapping[str, str] | None = None) -> str:
    """L'adresse de l'API locale — le port de la purge, jamais un second réglage."""
    env = None if environnement is None else dict(environnement)
    return f"http://127.0.0.1:{port_api(env)}"


class ClientAPI:
    """Les verbes du banc au-dessus d'un transport : un seul endroit qui connaît l'API.

    ## Deux délais, selon qui rédige la réponse (#1232)

    La plupart des routes répondent sans le modèle, et le délai ordinaire du
    transport leur suffit. Quatre ne le peuvent pas : `envoyer` et
    `envoyer_en_direct` (le fil juge le message et rédige sa réponse, rendue
    d'un coup ou au fil de l'eau), `proposition_equipe` (un playbook rédigé par
    rôle, #257) et `declarer_par_le_fil` (la réponse rédigée sur le projet
    déclaré, et la lecture d'un dossier importé, #1294). Elles durent ce que dure
    le modèle, et **l'écran ne les borne pas** : un banc qui les coupait à 30 s
    jugeait un produit plus pressé que celui qu'un utilisateur a sous les yeux.
    Mesuré le 2026-09-23 : la proposition d'équipe a tenu en 17 s, puis ≈ 26 s,
    puis a dépassé 30 s sur la première requête d'une API qui venait de démarrer
    — et S1 n'a jamais envoyé sa demande.

    Ces quatre verbes reçoivent donc `delai_modele_s`, la borne que le banc accorde
    déjà au modèle pour un run (`--delai`, `DELAI_RUN_S`) : une borne contre une
    API figée, pas une attente. Le classement se fait **ici, sur le code des
    routes** — `trancher_cadrage` et `recruter` n'appellent aucun modèle et
    gardent le délai ordinaire —, et il se revoit quand une route change de
    nature.
    """

    def __init__(self, transport: Transport, *, delai_modele_s: float = DELAI_RUN_S) -> None:
        self._transport = transport
        self._delai_modele_s = delai_modele_s

    # --- Le socle -------------------------------------------------------

    def _appel(
        self,
        methode: str,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        attendus: Sequence[int] = (200, 201),
        delai_s: float | None = None,
    ) -> Any:
        """Joue la requête et rend son corps — tout statut inattendu lève."""
        reponse = self._transport.demander(
            methode, chemin, corps=corps, params=params, delai_s=delai_s
        )
        if reponse.statut not in attendus:
            raise ErreurAPI(
                f"{methode} {chemin} → {reponse.statut} : {reponse.texte[:400]}",
                statut=reponse.statut,
                chemin=chemin,
            )
        return reponse.corps

    def sante(self) -> bool:
        """L'API répond-elle ? Une réponse d'erreur compte : ce qu'on veut savoir
        est si un process sert ce port, pas s'il va bien (même règle que la purge)."""
        try:
            return self._transport.demander("GET", "/api/sante").statut > 0
        except ErreurAPI:
            return False

    def espace(self) -> str:
        """L'espace de données que sert l'API (#1164) — `""` si elle ne le dit pas.

        C'est ce que le banc confronte au banc de sa copie avant de sauver un
        état : sauver les données d'une autre stack que celle où il a joué ferait
        rouvrir un état qui n'est pas celui du passage.
        """
        try:
            reponse = self._transport.demander("GET", "/api/sante")
        except ErreurAPI:
            return ""
        corps = reponse.corps if isinstance(reponse.corps, Mapping) else {}
        return str(corps.get("espace") or "")

    # --- Les projets ----------------------------------------------------

    def declarer_projet(self, nom: str, racine: str, *, origine: str) -> str:
        """Déclare un projet jetable et rend son identifiant."""
        fiche = self._appel(
            "POST", "/api/projets", corps={"nom": nom, "racine": racine, "origine": origine}
        )
        return str(fiche["id"])

    def retirer_projet(self, projet_id: str) -> None:
        """Oublie la déclaration — le dossier sur le disque, lui, reste (#221)."""
        self._appel("DELETE", f"/api/projets/{projet_id}", attendus=(200, 404))

    def projets(self) -> list[dict[str, Any]]:
        """Les projets déclarés, tels que l'API les sert — racine canonicalisée, VCS constaté."""
        fiches: list[dict[str, Any]] = list(self._appel("GET", "/api/projets") or [])
        return fiches

    # --- L'équipe -------------------------------------------------------

    def proposition_equipe(
        self, projet_id: str, *, renfort: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """L'équipe que l'analyse du projet appelle (#1039) — rien n'est créé ici.

        Le modèle en rédige les playbooks : la marge du modèle, pas le délai ordinaire.

        `renfort` (#1227) demande **le seul rôle** qu'un run a proposé dans le fil
        — son gabarit et sa raison, tels que la demande les porte —, exactement
        comme la carte du fil le rapporte à la route (`EquipeDansLeFil`).
        """
        propose: dict[str, Any] = self._appel(
            "POST",
            f"/api/projets/{projet_id}/equipe/proposition",
            corps={"renfort": dict(renfort)} if renfort is not None else None,
            delai_s=self._delai_modele_s,
        )
        return propose

    def creer_equipe(self, projet_id: str, validee: Mapping[str, Any]) -> dict[str, Any]:
        """Crée l'équipe validée par la voie de l'étape d'équipe (#1040)."""
        rapport: dict[str, Any] = self._appel(
            "POST", f"/api/projets/{projet_id}/equipe", corps=validee
        )
        return rapport

    # --- Le fil ---------------------------------------------------------

    def ouvrir_conversation(self) -> str:
        """Une conversation neuve sur le fil de l'orchestrateur.

        Neuve à chaque scénario, et c'est une condition de l'oracle : la demande
        de cadrage comme celle de recrutement sont des propriétés de la **suite**
        des messages (`proposition_en_attente`), donc un fil déjà chargé ferait
        trancher une demande qui n'est pas celle du scénario.
        """
        corps = self._appel("POST", f"{FIL}/conversations")
        return str(corps["conversation"]["id"])

    def envoyer(self, contenu: str, *, projet_id: str, conversation: str) -> dict[str, Any]:
        """Envoie un message d'utilisateur et rend **la réponse de l'agent**.

        Le modèle la rédige : la marge du modèle, pas le délai ordinaire.
        """
        corps = self._appel(
            "POST",
            f"{FIL}/messages",
            corps={"contenu": contenu, "projet_id": projet_id, "conversation": conversation},
            delai_s=self._delai_modele_s,
        )
        return _reponse_de(corps, chemin=f"{FIL}/messages")

    def envoyer_en_direct(
        self, contenu: str, *, projet_id: str, conversation: str
    ) -> Echange:
        """Envoie un message **par le flux que l'écran emprunte**, et rend l'échange reçu (#1265).

        `POST …/flux`, même corps que `envoyer` : c'est le même échange côté
        produit, rendu au fil de l'eau. Le banc en garde chaque trame datée, parce
        que le direct (#1222) ne se voit que là.

        Le modèle rédige la réponse : la marge du modèle, comme `envoyer`. Trois
        issues ne rendent aucune réponse, et chacune lève `ErreurAPI` — c'est-à-dire
        un **empêchement**, jamais un rouge du fil : un refus avant la première
        trame (statut HTTP), une trame `erreur` (aucune réponse ne viendra — le
        pendant du 502 de `POST …/messages`), un flux clos sans sa trame `fin`.
        """
        chemin = f"{FIL}/flux"
        echange = self._transport.flux(
            chemin,
            corps={"contenu": contenu, "projet_id": projet_id, "conversation": conversation},
            delai_s=self._delai_modele_s,
        )
        if not echange.ok:
            raise ErreurAPI(
                f"POST {chemin} → {echange.statut} : {echange.texte[:400]}",
                statut=echange.statut,
                chemin=chemin,
            )
        cloture = echange.cloture
        if cloture is not None and cloture.type == FRAGMENT_CHAT_ERREUR:
            raise ErreurAPI(
                f"le fil n'a pas pu répondre (POST {chemin}) : "
                f"{str(cloture.donnees.get('delta') or '')[:400]}",
                chemin=chemin,
            )
        if cloture is None or cloture.type != FRAGMENT_CHAT_FIN:
            vu = cloture.type if cloture is not None else "aucune trame"
            arret = " (arrêté)" if vu == FRAGMENT_CHAT_INTERROMPU else ""
            raise ErreurAPI(
                f"le flux de POST {chemin} s'est clos sans sa trame de fin "
                f"(dernière : {vu}{arret})",
                chemin=chemin,
            )
        return echange

    def fil(self, conversation: str) -> list[dict[str, Any]]:
        """Les messages **persistés** d'une conversation, dans l'ordre d'écriture (#1224).

        La seule lecture du banc qui ne suive pas une requête qu'il a faite : le
        récit de fin d'un run n'est la réponse à rien — personne ne l'a demandé,
        il paraît quand le run se termine. Le relire dans le fil est donc la
        seule façon de constater qu'il est là.
        """
        corps = self._appel("GET", FIL, params={"conversation": conversation})
        messages: list[dict[str, Any]] = list((corps or {}).get("messages") or [])
        return messages

    def recruter(
        self, *, conversation: str, validee: Mapping[str, Any], approuve: bool = True
    ) -> dict[str, Any]:
        """Valide l'équipe que le fil propose (#1146) et rend la réponse qui suit."""
        corps = self._appel(
            "POST",
            f"{FIL}/recrutement",
            corps={"approuve": approuve, "conversation": conversation, **dict(validee)},
        )
        return _reponse_de(corps, chemin=f"{FIL}/recrutement")

    def trancher_cadrage(
        self,
        *,
        conversation: str,
        projet_id: str,
        approuve: bool = True,
        bornes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Donne l'accord au cadrage proposé, bornes comprises (#990), et rend la réponse."""
        corps = self._appel(
            "POST",
            f"{FIL}/cadrage",
            corps={
                "approuve": approuve,
                "projet_id": projet_id,
                "conversation": conversation,
                **dict(bornes or {}),
            },
        )
        return _reponse_de(corps, chemin=f"{FIL}/cadrage")

    def declarer_par_le_fil(self, *, conversation: str, approuve: bool = True) -> dict[str, Any]:
        """Accepte — ou refuse — le projet que le fil propose (#1294), et rend la réponse.

        Le modèle rédige ce qui suit la déclaration, et lit un dossier importé
        (#1158) : la marge du modèle, pas le délai ordinaire.
        """
        corps = self._appel(
            "POST",
            f"{FIL}/projet",
            corps={"approuve": approuve, "conversation": conversation},
            delai_s=self._delai_modele_s,
        )
        return _reponse_de(corps, chemin=f"{FIL}/projet")

    # --- Les runs -------------------------------------------------------

    def execution(self, run_id: str, *, projet_id: str) -> dict[str, Any]:
        """L'état d'un run : statut, cause, coût, trace."""
        detail: dict[str, Any] = self._appel(
            "GET", f"/api/executions/{run_id}", params={"projet": projet_id}
        )
        return detail

    def validations(self, *, projet_id: str) -> list[dict[str, Any]]:
        """Les demandes d'arbitrage du projet (#48) — celles qui suspendent un run."""
        demandes: list[dict[str, Any]] = self._appel(
            "GET", "/api/validations", params={"projet": projet_id}
        )
        return demandes

    def decider(self, tache_id: str, *, approuve: bool = True) -> None:
        """Tranche un arbitrage en attente — 409 si quelqu'un l'a déjà tranché."""
        self._appel(
            "POST",
            f"/api/validations/{tache_id}/decision",
            corps={"approuve": approuve, "motif": ""},
            attendus=(200, 409),
        )


def _reponse_de(corps: Any, *, chemin: str) -> dict[str, Any]:
    """Le message **de l'agent** dans la paire que rendent les routes du fil.

    Les trois routes rendent `messages: [ce que l'utilisateur a fait, la réponse]`.
    Le banc lit toujours la seconde moitié : c'est elle qui porte la demande de
    cadrage, la demande d'équipe et le `run_id`.
    """
    messages = (corps or {}).get("messages") or []
    if len(messages) < 2:
        raise ErreurAPI(
            f"réponse inattendue de {chemin} : {len(messages)} message(s) au lieu de 2",
            chemin=chemin,
        )
    reponse: dict[str, Any] = messages[-1]
    return reponse


def equipe_validee(proposition: Mapping[str, Any]) -> dict[str, Any]:
    """L'équipe proposée, **rapportée telle quelle** au geste de validation.

    La forme de `POST /api/projets/{id}/equipe`, parce que c'est la même création
    (#1040) : ce qui repart est ce qui a été montré. Le banc ne retouche aucun
    rôle — il joue un utilisateur qui accepte la proposition, et tout ajustement
    inventé ici ferait juger autre chose que ce que l'analyse a recommandé.
    """
    return {
        "proposition_id": proposition.get("id", ""),
        "roles": [
            {
                "nom": role["nom"],
                "role": role["role"],
                "competences": role["competences"],
                "playbook": role["playbook"],
                "instances": role["instances"],
                "gabarit": role["gabarit"],
                "skills": [
                    {"nom": s["nom"], "chemin": s["chemin"], "commandes": s["commandes"]}
                    for s in role["skills"]
                ],
                "politique": role["politique"],
            }
            for role in proposition.get("roles") or []
        ],
    }


def cle_demande(demande: Mapping[str, Any]) -> tuple[str, str, str]:
    """L'identité d'une demande de validation aux yeux du banc — tâche **et** acte (#1198).

    Le pendant de `maestro.deliberation.cle_acte` de ce côté-ci de l'API : la
    tâche situe la demande, l'acte la distingue de la suivante. Les arguments
    sont sérialisés **clés triées**, pour que deux lectures du même acte donnent
    la même clé quel que soit l'ordre rendu par le JSON.

    Une demande qui ne porte pas d'acte — validation de tâche, accord
    d'écriture — se réduit à sa tâche, exactement comme avant : elle est seule de
    son espèce sur cette tâche.
    """
    arguments = demande.get("arguments")
    return (
        str(demande.get("tache_id") or ""),
        str(demande.get("outil") or ""),
        json.dumps(arguments, sort_keys=True, ensure_ascii=False) if arguments else "",
    )


def attendre_le_run(
    client: ClientAPI,
    run_id: str,
    *,
    projet_id: str,
    delai_s: float,
    note: Callable[[str, str], None],
    horloge: Callable[[], float] = time.monotonic,
    dormir: Callable[[float], None] = time.sleep,
    intervalle_s: float = INTERVALLE_SUIVI_S,
    arbitrages: list[str] | None = None,
) -> dict[str, Any]:
    """Suit un run jusqu'à son issue et rend son détail — arbitrages tranchés au passage.

    Le banc **approuve** les arbitrages d'action sensible (#571) de *son* run, et
    de lui seul (`run_id` porté par la demande depuis #570). Ce n'est pas une
    liberté prise : le projet est jetable, il vient d'être déclaré par le banc, et
    le geste attendu d'un utilisateur qui a demandé « vide ce dossier » est
    exactement celui-là. Ne pas le poser laisserait tous les scénarios d'action
    expirer sur une attente — ce qui dirait « le produit ne sait pas agir » là où
    il attend simplement qu'on lui réponde. Chaque approbation est **notée** au
    déroulé : le rapport dit ce que le banc a tranché.

    ⚠ **Ce qui est tranché s'identifie par l'acte, jamais par la tâche** (#1198).
    La file des validations s'indexe par tâche et l'assume (une demande y
    remplace la précédente, `maestro.controltower.state`), si bien qu'un banc qui
    retiendrait le `tache_id` ne répondrait qu'à la **première** demande d'une
    tâche : les suivantes resteraient devant lui sans qu'il les voie, et
    expireraient à la borne d'arbitrage. Mesuré le 2026-09-22 sur S1 — cinq
    demandes, une seule approuvée, run rouge. L'identité retenue est donc celle
    que le moteur utilise pour ses propres décisions (`maestro.deliberation`) :
    la tâche **et** l'acte. Deux appels au même acte ne reviennent de toute façon
    pas ici, le moteur leur servant la décision qu'il a gardée.

    `arbitrages` (#1226) reçoit l'**outil** de chaque demande tranchée, dans
    l'ordre. Ce n'est pas une commodité de trace : un scénario dont l'oracle est
    « personne n'a eu à trancher une commande » a besoin de compter ce que le banc
    a tranché à la place de la personne, et le déroulé, lui, se lit mais ne se
    compte pas. Une demande sans outil — validation de tâche, accord d'écriture —
    y entre sous une chaîne vide : ce n'est pas une validation de commande, et la
    taire ferait perdre le total.

    À l'expiration du délai, le dernier état lu est rendu tel quel : c'est à
    l'oracle de juger qu'un run encore en vol n'est pas un run abouti.
    """
    limite = horloge() + delai_s
    tranchees: set[tuple[str, str, str]] = set()
    detail = client.execution(run_id, projet_id=projet_id)
    while True:
        statut = str(detail.get("statut") or "")
        if statut in STATUTS_EXECUTION_TERMINAUX:
            return detail
        if statut == EXECUTION_EN_ATTENTE_ARBITRAGE:
            for demande in client.validations(projet_id=projet_id):
                if (
                    str(demande.get("run_id") or "") == run_id
                    and str(demande.get("statut") or "") == VALIDATION_EN_ATTENTE
                    and cle_demande(demande) not in tranchees
                ):
                    tache = str(demande["tache_id"])
                    tranchees.add(cle_demande(demande))
                    client.decider(tache, approuve=True)
                    if arbitrages is not None:
                        arbitrages.append(str(demande.get("outil") or ""))
                    note(
                        "arbitrage approuvé",
                        f"{tache} — {demande.get('titre') or demande.get('outil') or ''}",
                    )
        if horloge() >= limite:
            return detail
        dormir(intervalle_s)
        detail = client.execution(run_id, projet_id=projet_id)
