"""Le **mode local durci** de l'API Control Tower (#638) : un jeton, des origines.

L'API écoute sur la boucle locale et, jusqu'ici, n'avait **aucune
authentification** et servait `allow_origins=["*"]`. C'était sans conséquence
tant que le produit ne touchait pas au disque de l'utilisateur ; depuis qu'un
projet a une **racine sur le disque** (#221) et qu'un run y écrit, n'importe
quelle page ouverte dans le navigateur pouvait lancer un run chez lui
([docs/24 §6](../../docs/24-projets-locaux-et-poste-de-travail.md) point 3,
[docs/19 §2.5](../../docs/19-securite-modele-de-menace.md)).

Deux gardes, et elles ne protègent pas de la même chose :

- **le jeton** ferme l'API à tout appelant qui ne l'a pas — une autre page du
  navigateur comme un autre programme du poste. C'est la garde qui compte ;
- **les origines** ferment le navigateur : un `Origin` hors liste ne reçoit
  aucun en-tête CORS, donc la page tierce ne peut pas lire la réponse. Seule,
  elle n'arrête personne (un client non navigateur écrit l'`Origin` qu'il veut)
  — c'est pourquoi les deux vivent ensemble.

## Ce que le jeton est, et où il vit

Un secret d'URL-safe base64 (`secrets.token_urlsafe`), **engendré au premier
démarrage** et persisté **hors du dépôt** : `~/.maestro/jeton-api`, en `0600`.
Hors du dépôt parce qu'un secret n'a rien à faire dans un arbre Git, et dans le
dossier personnel parce que c'est le périmètre que l'OS garde déjà pour
l'utilisateur — le même raisonnement que le coffre des secrets par agent
(docs/18), à un cran plus bas.

La lecture et l'écriture passent par `jeton_local()`, **idempotente** : le
premier appelant l'engendre, les suivants le relisent. C'est ce qui permet à
l'API et aux outils locaux du dépôt (le banc, la purge, les captures, la
relecture visuelle) de s'accorder **sans se parler** — ils lisent le même
fichier, au même titre que deux programmes du même utilisateur.

`MAESTRO_API_JETON` impose un jeton et court-circuite le fichier : c'est la
porte du mode serveur (un secret injecté par le déploiement) et celle des
outils qui reçoivent le jeton de leur appelant, `scripts/design/relecture-projets.py`
en tête — il n'a que la bibliothèque standard et ne peut rien importer d'ici.

## Les deux régimes, et pourquoi l'ouvert se nomme

`MAESTRO_API_AUTH` vaut `jeton` (le défaut, l'API sert durcie) ou `ouvert`.
Le régime ouvert **se nomme** : c'est la voie des usages de développement, et
elle ne doit jamais être ce qu'on obtient sans rien dire — l'inverse ferait
croire à une API fermée quand elle est grande ouverte. Même parti pris que
`MAESTRO_HOTE_RUN` (#446) et `MAESTRO_ISOLATION` (#108), valeur inconnue
comprise : une frontière mal orthographiée est une **erreur franche**, jamais
un repli silencieux.

⚠ `create_app()` construit une app **ouverte** quand on ne lui passe pas de
politique : c'est la configuration des tests (170 suites qui montent l'app sans
rien savoir d'un jeton), et elle est explicite. La **production** passe par
`create_default_app`, qui résout la politique ici et sert donc durcie.

## Les origines, et leur défaut

`MAESTRO_API_ORIGINES` est une liste séparée par des virgules — `*` y est
admis, et c'est alors une décision écrite. Sans réglage, le défaut **local**
est l'origine du front : `http://localhost:<MAESTRO_PORT_UI>` et son jumeau en
`127.0.0.1`, les deux écritures de la même page (un worktree sert le front sur
son propre port, #847). Le mode serveur se règle par la même variable : il n'y a
pas de seconde branche de code pour lui (**ENF-12**).

## Le WebSocket, et le paramètre d'URL

Un navigateur ne pose **aucun en-tête** sur une poignée de main WebSocket : le
jeton y voyage donc en paramètre d'URL (`?jeton=`). C'est le prix du protocole,
et il est borné — l'URL ne quitte pas la boucle locale, et le paramètre n'est
accepté **que** sur le WebSocket : une route REST qui l'admettrait ferait
voyager le secret dans des journaux d'accès et des historiques de navigation.

## Les journaux, où l'URL s'écrit entière (#1292)

La boucle locale ne suffisait pas : le serveur consigne l'URL de chaque
poignée de main, et `api.log` gardait le jeton en clair. C'est un garde-fou
« secrets », pas une bride : **aucun journal de l'API ne porte le jeton**.
`FiltreDuJeton` se pose sur chaque gestionnaire du serveur (`maestro-api` le
câble, `maestro.controltower.cli`) et masque, dans le message, ses arguments et
la trace d'une exception, deux choses : la valeur de **tout** paramètre
`jeton=` — un jeton faux ou périmé reste un secret tenté — et **le jeton
lui-même**, où qu'il paraisse. Le reste de la ligne ne bouge pas : chemin,
autres paramètres, code de la réponse, ce qu'on vient y lire pour comprendre un
refus. Le masque (`MASQUE_JETON`) est hors de l'alphabet du jeton, si bien
qu'un journal masqué ne se confond jamais avec un journal fautif.

Le paramètre d'URL reste, lui : c'est le prix du protocole (voir plus haut), et
le masquer à l'écriture couvre tout client — le front, un outil du poste, un
script — là où un autre transport ne couvrirait que celui qu'on réécrit. Les
journaux écrits avant ce masquage ne sont **pas réécrits** : le lanceur de
développement (`scripts/controltower/start.sh`) nomme ceux qui portent encore
le jeton, et c'est à la personne de les supprimer.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from maestro.config import ConfigError, Settings, load_settings

#: Les deux régimes d'accès. `jeton` est le défaut de l'API servie.
REGIME_JETON = "jeton"
REGIME_OUVERT = "ouvert"
REGIMES = (REGIME_JETON, REGIME_OUVERT)

#: L'en-tête standard du jeton et son préfixe (`Authorization: Bearer <jeton>`).
ENTETE_JETON = "authorization"
PREFIXE_JETON = "Bearer "

#: Le paramètre d'URL du jeton, **réservé au WebSocket** (voir l'en-tête).
PARAM_JETON = "jeton"

#: Ce qui remplace le jeton dans un journal (#1292). Hors de l'alphabet du jeton
#: (base64 URL-safe) : un balayage distingue un journal masqué d'un journal fautif.
MASQUE_JETON = "***"

#: La valeur d'un paramètre `jeton=` dans un texte — l'URL d'une ligne de
#: journal. Le nom ne se lit qu'en entier (`monjeton=` n'en est pas un), la
#: casse est ignorée (un client qui l'écrit mal y a quand même mis le secret), et
#: la valeur court jusqu'au paramètre suivant, à la fin de l'URL ou du guillemet.
_VALEUR_DU_PARAMETRE = re.compile(
    rf"(?<![\w.~%-])({re.escape(PARAM_JETON)}=)[^&\s\"'#]+", re.IGNORECASE
)

#: Le port du front quand rien ne le dit — celui de `scripts/controltower/start.sh`.
PORT_UI_DEFAUT = 3000

#: Le fichier du jeton, hors du dépôt : `~/.maestro/jeton-api`.
DOSSIER_JETON = ".maestro"
FICHIER_JETON = "jeton-api"

#: Les octets du secret engendré (43 caractères en base64 URL-safe).
OCTETS_JETON = 32

#: Le motif du refus, servi tel quel dans le `detail` d'un 401. Il s'adresse à
#: qui lit une réponse d'API, pas à qui développe : ni numéro de ticket ni
#: fichier du dépôt — la garde de `tests/test_registre_de_langue.py`.
MOTIF_REFUS = (
    "Jeton d'API absent ou invalide : cette API n'accepte que les appels "
    "authentifiés. L'interface l'envoie d'elle-même ; un outil du poste le lit "
    "dans ~/.maestro/jeton-api."
)

#: Le code de fermeture du WebSocket refusé (violation de politique, RFC 6455).
CODE_FERMETURE = 1008


@dataclass(frozen=True)
class PolitiqueAcces:
    """Ce que l'API accepte : un jeton (ou aucun) et des origines nommées.

    `jeton` à `None` est le régime **ouvert** — aucune requête n'est refusée ici.
    `origines` vaut `("*",)` pour « toutes », et c'est toujours une décision
    écrite : le défaut résolu par `politique_depuis` est l'origine du front.
    """

    jeton: str | None = None
    origines: tuple[str, ...] = ()

    @classmethod
    def ouverte(cls) -> PolitiqueAcces:
        """L'API sans garde — la configuration des tests, jamais celle servie.

        Origines à `*` : une suite qui monte l'app n'a pas d'origine de front à
        déclarer, et le portier n'est de toute façon pas armé sans jeton.
        """
        return cls(jeton=None, origines=("*",))

    @property
    def durci(self) -> bool:
        """Vrai quand un jeton est exigé — le portier n'est armé que dans ce cas."""
        return self.jeton is not None

    @property
    def regime(self) -> str:
        """Le nom du régime, celui de `MAESTRO_API_AUTH`."""
        return REGIME_JETON if self.durci else REGIME_OUVERT

    def porte_le_jeton(self, presente: str | None) -> bool:
        """Le jeton présenté est-il le bon ? Comparaison **en temps constant**.

        `hmac.compare_digest` plutôt que `==` : une égalité qui sort au premier
        octet différent mesure le préfixe commun, et un attaquant local a tout
        le loisir de rejouer. Le coût est nul, l'oubli se rattrape mal.
        """
        if not self.durci:
            return True
        if not presente:
            return False
        assert self.jeton is not None  # `durci` vient de le dire
        return hmac.compare_digest(presente, self.jeton)

    def origine_admise(self, origine: str | None) -> bool:
        """L'origine de la requête est-elle dans la liste ?

        Une requête **sans** `Origin` est admise : ce n'est pas un navigateur,
        donc la liste ne la concerne pas — c'est le jeton qui la juge. Refuser
        ici ferait croire à une garde que l'en-tête, forgeable, n'offre pas.
        """
        if "*" in self.origines:
            return True
        if origine is None:
            return True
        return origine.rstrip("/") in self.origines

    def annonce(self) -> str:
        """La ligne que l'API imprime à son démarrage — le régime, et rien du secret."""
        origines = ", ".join(self.origines) if self.origines else "aucune"
        if not self.durci:
            return (
                f"[acces] régime « {REGIME_OUVERT} » — aucun jeton exigé "
                f"(MAESTRO_API_AUTH) · origines : {origines}"
            )
        return (
            f"[acces] régime « {REGIME_JETON} » — jeton exigé sur chaque requête "
            f"· origines : {origines}"
        )


class GardeAcces:
    """Le portier de l'API locale : un middleware ASGI, **avant** toute route.

    ASGI pur et non une dépendance FastAPI, pour deux raisons qui tiennent
    ensemble : une dépendance ne couvre que les routes qui la déclarent — donc
    jamais celle qu'on ajoutera demain en oubliant de l'écrire —, et elle ne
    couvre **pas le WebSocket**, qui est précisément ce qui pousse les
    événements de tous les projets du poste.

    Il n'a **aucune exemption de route** : le jeton se demande sur `/api/sante`
    comme sur le reste. Une sonde de vitalité exemptée serait le premier
    précédent, et un 401 dit déjà « quelque chose sert ce port » — ce que les
    sondes du dépôt cherchent à savoir (`purge.api_repond` compte d'ailleurs
    une réponse d'erreur comme une réponse).

    Le middleware CORS est monté **après** lui, donc **au-dessus** : un
    préflight `OPTIONS` est tranché par CORS et n'arrive jamais ici (un
    navigateur ne porte pas d'`Authorization` sur un préflight), et un refus
    d'ici ressort avec ses en-têtes CORS — sans quoi la page ne pourrait pas
    lire le motif du 401.
    """

    def __init__(self, app: Any, politique: PolitiqueAcces) -> None:
        self._app = app
        self._politique = politique

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Laisse passer, ou refuse — dans la langue du protocole visé."""
        type_scope = scope.get("type")
        if not self._politique.durci or type_scope not in {"http", "websocket"}:
            await self._app(scope, receive, send)
            return
        entetes = _entetes_du_scope(scope)
        if type_scope == "websocket":
            if not self._politique.origine_admise(entetes.get("origin")):
                await _refus_websocket(receive, send)
                return
            presente = _jeton_de_l_entete(entetes) or _jeton_du_parametre(scope)
            if not self._politique.porte_le_jeton(presente):
                await _refus_websocket(receive, send)
                return
            await self._app(scope, receive, send)
            return
        if not self._politique.porte_le_jeton(_jeton_de_l_entete(entetes)):
            await _refus_http(send)
            return
        await self._app(scope, receive, send)


def _entetes_du_scope(scope: dict[str, Any]) -> dict[str, str]:
    """Les en-têtes de la requête, noms en minuscules (ASGI les rend en octets)."""
    return {
        nom.decode("latin-1").lower(): valeur.decode("latin-1")
        for nom, valeur in scope.get("headers") or ()
    }


def _jeton_de_l_entete(entetes: dict[str, str]) -> str | None:
    """Le jeton porté par `Authorization: Bearer …`, ou `None`."""
    brut = entetes.get(ENTETE_JETON, "")
    if not brut.lower().startswith(PREFIXE_JETON.lower()):
        return None
    return brut[len(PREFIXE_JETON) :].strip() or None


def _jeton_du_parametre(scope: dict[str, Any]) -> str | None:
    """Le jeton porté par `?jeton=` — **WebSocket seul** (voir l'en-tête du module)."""
    brute = scope.get("query_string") or b""
    valeurs = parse_qs(brute.decode("latin-1")).get(PARAM_JETON) or []
    return valeurs[-1].strip() or None if valeurs else None


async def _refus_http(send: Any) -> None:
    """Le 401 motivé, en JSON — le `detail` que le front sait déjà afficher."""
    corps = json.dumps({"detail": MOTIF_REFUS}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(corps)).encode("latin-1")),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": corps})


async def _refus_websocket(receive: Any, send: Any) -> None:
    """Refuse la poignée de main : on reçoit le `connect`, on ferme sans accepter.

    Fermer sans accepter plutôt qu'accepter pour dire le motif, à l'inverse du
    refus de portée (#277) : là-bas le client est identifié et on lui explique
    ce qu'il a mal demandé, ici il n'a rien à apprendre de nous.
    """
    await receive()
    await send({"type": "websocket.close", "code": CODE_FERMETURE})


def masquer_le_jeton(texte: str, jeton: str | None = None) -> str:
    """Le texte sans secret : tout paramètre `jeton=` masqué, et le jeton lui-même.

    Idempotente — un texte masqué le reste —, si bien que deux gestionnaires qui
    filtrent le même enregistrement ne se gênent pas. Le jeton se masque **par
    sa valeur** en plus du paramètre : c'est ce qui permet de promettre qu'il
    n'est nulle part, et pas seulement là où on l'attendait.
    """
    masque = _VALEUR_DU_PARAMETRE.sub(rf"\g<1>{MASQUE_JETON}", texte)
    if jeton:
        masque = masque.replace(jeton, MASQUE_JETON)
    return masque


class FiltreDuJeton(logging.Filter):
    """Le filtre de journalisation qui tient le jeton hors des journaux (#1292).

    Il se pose sur un **gestionnaire**, pas sur un logger : le filtre d'un
    logger ne voit pas les enregistrements que ses enfants lui propagent, et
    celui d'un gestionnaire voit tout ce qu'il écrit. Il **réécrit** et ne
    retient rien — une ligne masquée sert encore le diagnostic, une ligne
    supprimée ne sert plus personne.

    Les arguments sont masqués **un à un** plutôt que la ligne rendue : le
    formateur d'un journal d'accès les relit par position (client, méthode,
    chemin, version, code), et les fondre dans le message le casserait. Un
    argument qui n'est pas du texte n'est remplacé que si sa forme écrite
    portait un secret — sinon un `%d` recevrait une chaîne.
    """

    def __init__(self, jeton: str | None = None) -> None:
        super().__init__()
        self._jeton = jeton

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - API de logging
        if isinstance(record.msg, str):
            record.msg = self._masquer(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._masquer_argument(argument) for argument in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {
                cle: self._masquer_argument(valeur) for cle, valeur in record.args.items()
            }
        # La trace d'une exception est rendue ici, masquée, puis gardée : le
        # formateur réutilise `exc_text` au lieu de la recalculer en clair.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._masquer(record.exc_text)
        if record.stack_info:
            record.stack_info = self._masquer(record.stack_info)
        return True

    def _masquer(self, texte: str) -> str:
        return masquer_le_jeton(texte, self._jeton)

    def _masquer_argument(self, argument: Any) -> Any:
        if isinstance(argument, str):
            return self._masquer(argument)
        ecrit = str(argument)
        masque = self._masquer(ecrit)
        return argument if masque == ecrit else masque


def chemin_du_jeton(settings: Settings | None = None) -> Path:
    """Le fichier du jeton — `MAESTRO_API_JETON_FICHIER`, sinon `~/.maestro/jeton-api`.

    Lève `ConfigError` quand l'OS ne sait pas dire où est le dossier personnel
    (conteneurs sans `HOME`) : le geste est alors de nommer le fichier, et le
    dire vaut mieux qu'écrire un jeton dans un dossier au hasard.
    """
    settings = settings or load_settings()
    if settings.api_jeton_fichier:
        return Path(settings.api_jeton_fichier).expanduser()
    try:
        maison = Path.home()
    except (RuntimeError, OSError) as exc:  # pragma: no cover - dépend de l'environnement
        raise ConfigError(
            "Dossier personnel introuvable : nommer le fichier du jeton d'API "
            "(MAESTRO_API_JETON_FICHIER) ou le jeton lui-même (MAESTRO_API_JETON)."
        ) from exc
    return maison / DOSSIER_JETON / FICHIER_JETON


def jeton_local(settings: Settings | None = None) -> str:
    """Le jeton de cette machine — relu s'il existe, **engendré** sinon.

    Idempotente, et c'est tout son intérêt : l'API et les outils locaux du dépôt
    s'accordent en lisant le même fichier, sans qu'aucun ait à naître avant
    l'autre. `MAESTRO_API_JETON` court-circuite le fichier (mode serveur, et les
    outils qui reçoivent le jeton de leur appelant).

    L'écriture est **exclusive** (`O_CREAT | O_EXCL`, mode `0600`) : deux
    process qui démarrent ensemble ne se marchent pas dessus — le perdant relit
    ce que le gagnant a écrit, plutôt que d'écraser un jeton déjà distribué.
    """
    settings = settings or load_settings()
    impose = (settings.api_jeton or "").strip()
    if impose:
        return impose
    chemin = chemin_du_jeton(settings)
    existant = _lire(chemin)
    if existant:
        return existant
    return _engendrer(chemin)


def origines_locales(port_ui: int) -> tuple[str, ...]:
    """Les deux écritures de l'origine du front local — `localhost` et `127.0.0.1`.

    Les deux, parce que ce sont deux origines distinctes pour le navigateur
    alors que c'est la même page : `start.sh` ouvre `http://localhost:<port>`,
    et les scripts du dépôt frappent `http://127.0.0.1:<port>`.
    """
    return (f"http://localhost:{port_ui}", f"http://127.0.0.1:{port_ui}")


def port_du_front(settings: Settings | None = None) -> int:
    """Le port du front (`MAESTRO_PORT_UI`), sinon celui de `start.sh`.

    Un worktree sert sa stack sur ses propres ports (docs/10 §9.1) : sans cette
    lecture, le défaut d'origines désignerait la stack d'une autre copie.
    """
    settings = settings or load_settings()
    brut = (settings.port_ui or "").strip()
    return int(brut) if brut.isdigit() else PORT_UI_DEFAUT


def origines_reglees(settings: Settings | None = None) -> tuple[str, ...]:
    """La liste des origines autorisées : le réglage, sinon l'origine du front.

    `MAESTRO_API_ORIGINES` est une liste séparée par des virgules ; `*` y est
    admis et vaut « toutes », ce qui devient alors une décision écrite plutôt
    qu'un défaut. Une valeur qui ne porte aucune origine lisible (` , `) est
    traitée comme absente : elle ne dit rien, et fermer l'API sur un réglage
    vide ferait chercher la panne ailleurs.
    """
    settings = settings or load_settings()
    brut = (settings.api_origines or "").strip()
    nettoyees = tuple(
        dict.fromkeys(
            morceau.strip().rstrip("/") for morceau in brut.split(",") if morceau.strip()
        )
    )
    return nettoyees or origines_locales(port_du_front(settings))


def politique_depuis(settings: Settings | None = None) -> PolitiqueAcces:
    """La politique d'accès de l'API servie — **durcie** sauf régime nommé.

    Résolue ici et nulle part ailleurs : `create_default_app` la passe à
    `create_app`, `maestro-api` l'annonce, les outils locaux en tirent leurs
    en-têtes. Un régime inconnu est une erreur franche (voir l'en-tête).
    """
    settings = settings or load_settings()
    regime = (settings.api_auth or REGIME_JETON).strip().lower()
    if regime not in REGIMES:
        raise ConfigError(
            f"MAESTRO_API_AUTH : régime inconnu {regime!r} "
            f"(attendu : {' | '.join(REGIMES)}, ou vide pour « {REGIME_JETON} »)."
        )
    origines = origines_reglees(settings)
    if regime == REGIME_OUVERT:
        return PolitiqueAcces(jeton=None, origines=origines)
    return PolitiqueAcces(jeton=jeton_local(settings), origines=origines)


def entetes_client(settings: Settings | None = None) -> dict[str, str]:
    """Les en-têtes qu'un outil local pose pour parler à l'API du poste.

    Vide en régime ouvert — l'API n'exige rien, et poser un jeton qu'elle
    ignore n'apprendrait rien à personne. C'est ce que le banc, la purge, les
    captures et la relecture visuelle appellent : une seule façon de porter le
    jeton, jamais un en-tête réécrit à la main.
    """
    settings = settings or load_settings()
    politique = politique_depuis(settings)
    if not politique.durci:
        return {}
    assert politique.jeton is not None
    return {"Authorization": f"{PREFIXE_JETON}{politique.jeton}"}


def _lire(chemin: Path) -> str:
    """Le jeton persisté, ou `""` — un fichier illisible vaut « pas de jeton »."""
    try:
        return chemin.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def _engendrer(chemin: Path) -> str:
    """Engendre le jeton, l'écrit en `0600`, et rend celui qui fait foi.

    En cas de course, le fichier existe déjà : on relit plutôt que d'écraser —
    le jeton du perdant aurait pu être distribué entre-temps.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    # Best-effort : sans effet sur Windows, et un dossier personnel qui refuse
    # le changement de mode n'est pas une raison de ne pas démarrer.
    try:
        chemin.parent.chmod(0o700)
    except OSError:  # pragma: no cover - dépend du système de fichiers
        pass
    jeton = secrets.token_urlsafe(OCTETS_JETON)
    try:
        descripteur = os.open(chemin, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        relu = _lire(chemin)
        if relu:
            return relu
        raise  # fichier présent mais vide : rien à relire, la panne se dit
    with os.fdopen(descripteur, "w", encoding="utf-8") as sortie:
        sortie.write(jeton + "\n")
    return jeton


def rendre_le_jeton(sortie: Any = None, erreur: Any = None) -> int:
    """Le **jeton** sur la sortie standard, le **régime** sur l'erreur standard.

    Ce dont `scripts/controltower/start.sh` a besoin, en un seul appel : il
    capture le premier pour le front, laisse le second s'afficher, et n'a ni à
    réécrire la résolution en shell ni à recopier la phrase du régime. En régime
    ouvert, rien sur la sortie standard et code `3` — « il n'y en a pas » n'est
    pas une panne.

    ⚠ **Ce module n'est pas un point d'entrée**, et c'est pourquoi cette
    fonction est appelée depuis `maestro.controltower.cli` (`maestro-api
    --jeton`) : le paquet l'importe déjà par `app`, si bien qu'un `python -m
    maestro.controltower.acces` l'exécuterait une seconde fois comme `__main__`
    et ferait précéder le jeton d'un `RuntimeWarning` — en tête du journal où
    l'on vient chercher la cause d'un démarrage raté. Même piège, même parade
    que `hote_detache` (voir l'en-tête du paquet).
    """
    flux = sys.stdout if sortie is None else sortie
    plainte = sys.stderr if erreur is None else erreur
    politique = politique_depuis()
    print(politique.annonce(), file=plainte)
    if not politique.durci:
        return 3
    print(politique.jeton, file=flux)
    return 0
