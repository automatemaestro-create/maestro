"""Battement de cœur d'un run — ce qui ne bat plus est signalé orphelin (#348).

Un run lancé depuis la Control Tower vit dans un **process détaché** depuis #446
(`maestro.controltower.hote_detache`) : il survit à l'arrêt de `maestro-api`,
mais pas au sommeil de sa machine, et c'est un choix assumé. Ce qui ne l'était
pas, c'est que sa **mort soit invisible** — le journal durable (#97) conserve le
dernier état publié, donc un run dont l'hôte est tombé reste `en_cours` pour
toujours. Au constat du 2026-08-17, quatre runs fantômes étaient affichés comme
actifs, dont deux du 22 juillet.

La déduction naïve — « l'API vient de démarrer, donc tout `en_cours` est mort » —
est **fausse** : un run lancé par `maestro-run --publier` publie sur le même Redis
sans passer par l'API, et serait déclaré mort à tort. D'où un signal porté par
l'**hôte** du run, sur le modèle de la carte du pilote (#213) et du verdict
d'orphelin de #327.

**La règle tient en une phrase, et c'est tout le module** : ce qui bat est
*vivant*, ce qui ne bat plus depuis le seuil est *orphelin*, ce qui n'a jamais
battu est *indéterminé*. Le troisième verdict n'est pas une commodité — c'est le
refus explicite de deviner : un run antérieur à ce lot n'a pas d'hôte muet, il a
un hôte dont personne n'a jamais écouté le cœur.

Deux moitiés, et leur asymétrie est celle du reste de la Control Tower :

- **Écrire** — un hôte pose un battement. Deux formes, comme le bus a un
  `RedisEventBus` asynchrone côté API et un `publieur_redis` **synchrone** côté
  producteur : `RegistreBattements` (asynchrone) pour l'API, qui vit déjà dans
  une boucle asyncio, et `CoeurRun` + `batteur_redis` / `oublieur_redis`
  (synchrones, un fil démon) pour les hôtes qui n'ont pas de boucle à eux —
  `maestro-run --publier` et le process détaché de #443. Une seule forme
  obligerait l'un des deux côtés à en fabriquer une.
- **Lire** — l'API relit tous les battements d'un coup (`battements()`) et
  `vitalite()` en tire le verdict d'un run. La lecture ne dépend d'aucun process
  particulier : c'est ce qui fait qu'un run publié hors de l'API reste reconnu
  vivant **pendant un redémarrage de l'API**, troisième exigence du ticket.

**Un battement ne s'efface jamais parce que l'hôte s'arrête** — il s'arrête, et
c'est le fait qu'on veut lire. Il s'efface quand un run est **soldé**, et à cette
seule condition : son statut est alors terminal, la question de sa vitalité ne se
pose plus, et l'entrée n'a plus qu'un coût de stockage.

⚠ **Ce qui a changé avec #446, c'est qui solde.** Tant que l'API était le seul à
publier un statut de fin, un run vivant hors d'elle (`maestro-run --publier`, puis
l'hôte détaché de #443) n'en publiait aucun : son dernier battement vieillissait
et le faisait apparaître `orphelin` **terminé normalement** — corollaire assumé,
le verdict portant sur son **hôte** (« plus personne ne veille sur ce run »)
jamais sur son travail, exactement comme `orphelin` pour un ticket dans #327.
Avec l'hôte détaché devenu le **défaut** des lancements Control Tower, ce
corollaire aurait porté sur *tous* les runs. Un hôte publie donc désormais son
issue en partant, et retire son battement dans le même geste
(`maestro.controltower.bridge.solder_le_run`).

Le corollaire ne disparaît pas pour autant, il **change de portée** : un run
survit à l'**accident**, pas à l'**extinction** — ni à sa machine (#486,
docs/28 §11 ; l'arrêt volontaire solde ses runs, l'arrêt subi ne les touche pas).
Ce qui reste `orphelin` est donc ce qui est mort sans pouvoir le dire — machine
endormie, process tué net, Redis muet au dernier instant — et c'est là,
exactement, que le verdict garde son sens : il signale ce que personne n'a soldé.
On le voit ici, on le ramasse quand l'API portait l'hôte
(`ServiceExecutions._ramasser`), et on le rattrape sur le brief (#349).
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime

from maestro.controltower.events import REDIS_URL_DEFAUT
from maestro.controltower.state import STATUTS_EXECUTION_TERMINAUX

#: Les trois verdicts de vitalité d'un run **non soldé** (#348) : son hôte bat
#: encore, il ne bat plus depuis le seuil, ou il n'a jamais battu. Un run soldé
#: n'en porte aucun (`None`) — la question ne se pose pas pour un run qui a rendu
#: son issue, et inventer un quatrième mot pour « sans objet » ferait chercher un
#: sens là où il n'y en a pas.
VITALITE_VIVANT = "vivant"
VITALITE_ORPHELIN = "orphelin"
VITALITE_INDETERMINE = "indetermine"

#: Période du battement, en secondes. Assez court pour que le seuil ci-dessous
#: absorbe des dizaines de battements manqués, assez long pour rester gratuit :
#: un `HSET` toutes les 30 s par run en vol, sur l'instance Redis déjà mutualisée
#: avec la file (#41), le bus (#46), les boîtes (#44) et le journal (#97).
PERIODE_BATTEMENT_S = 30.0

#: Seuil d'orphelinat, en secondes — **30 minutes**, soit soixante battements
#: consécutifs manqués. Volontairement généreux, pour deux raisons de nature
#: différente. La première est mesurée : une tâche d'agent peut travailler très
#: longtemps sans rien publier (26 min observées le 2026-08-14 sur une seule
#: tentative), et si un appel synchrone affame la boucle qui bat, le cœur d'un run
#: bien vivant s'arrête avec elle. La seconde est un rapport de coûts : rater un
#: orphelin coûte un run affiché `en_cours` un peu plus longtemps, déclarer
#: orphelin un run vivant coûte — au lot suivant, qui proposera de reprendre — de
#: repartir sur le cadrage d'un run qui travaille encore. Même arbitrage que le
#: seuil de six heures de #327, et même conclusion : on se trompe du côté qui ne
#: détruit rien.
SEUIL_ORPHELIN_S = 1800.0

#: Clé Redis du registre des battements — un **hash** `run_id → horodatage du
#: dernier battement`, sur l'instance mutualisée avec la file (#41), le bus (#46),
#: les boîtes (#44) et le journal durable (#97). Un hash et non N clés à TTL :
#: l'API lit tous les battements d'un coup (`HGETALL`) pour rendre `GET
#: /api/executions`, et surtout un TTL **effacerait** le battement périmé d'un run
#: mort, c'est-à-dire précisément le fait qu'on veut lire — l'expiration
#: transformerait chaque orphelin en indéterminé au bout d'un moment.
CLE_BATTEMENTS = "maestro.runs:battements"

_LOGGER = logging.getLogger("maestro.controltower")


def horodatage_battement(instant: datetime | None = None) -> str:
    """L'horodatage d'un battement — UTC ISO-8601, la forme des événements (#46)."""
    return (instant or datetime.now(UTC)).isoformat(timespec="seconds")


def vitalite(
    statut: str,
    dernier_battement: str | None,
    *,
    maintenant: datetime | None = None,
    seuil_s: float = SEUIL_ORPHELIN_S,
) -> str | None:
    """Le verdict de vitalité d'un run — `None` s'il est **soldé**.

    `statut` est celui de la projection (`EtatExecution.statut`) et
    `dernier_battement` l'horodatage lu au registre, `None` si le run n'y figure
    pas. Les deux attentes humaines (`en_attente_brief`, `en_attente_reponses`)
    ne sont **pas** terminales et reçoivent donc un verdict comme les autres :
    un run suspendu sur un humain est porté par un hôte, qui bat — c'est même le
    seul moyen de distinguer « personne n'a encore répondu » de « le process qui
    posait la question est mort », les deux cas de #347.

    Trois écarts à connaître, tous du même côté :

    - un horodatage **illisible** rend `indetermine` et non `orphelin` : une
      entrée corrompue n'apprend rien, et affirmer la mort sur une donnée qu'on
      ne sait pas lire serait affirmer plus que ce qu'on sait ;
    - un battement **dans le futur** (horloges désaccordées entre l'hôte et
      l'API) est traité comme frais — le contraire déclarerait orphelin un run
      qui vient de battre ;
    - un horodatage **sans fuseau** est lu en UTC, la seule forme que ce module
      écrit ; une valeur venue d'ailleurs y serait de toute façon plus proche du
      vrai que naïvement comparée à un instant aware (qui lèverait).
    """
    if statut in STATUTS_EXECUTION_TERMINAUX:
        return None
    if not dernier_battement:
        return VITALITE_INDETERMINE
    try:
        battu = datetime.fromisoformat(dernier_battement)
    except ValueError:
        return VITALITE_INDETERMINE
    if battu.tzinfo is None:
        battu = battu.replace(tzinfo=UTC)
    age = ((maintenant or datetime.now(UTC)) - battu).total_seconds()
    return VITALITE_VIVANT if age <= seuil_s else VITALITE_ORPHELIN


class RegistreBattements(ABC):
    """Le registre des battements — où un hôte pose son signal de vie (#348).

    `battre` pose (ou rafraîchit) le battement d'un run ; `battements` rend tous
    ceux connus, `run_id → horodatage`, en **un** appel — c'est la lecture que
    fait `GET /api/executions`, qui a N runs à juger et pas un ; `oublier` retire
    l'entrée d'un run soldé, seul effacement du dispositif (cf. l'en-tête du
    module).

    Même contrat à deux implémentations que le bus (`EventBus`), les boîtes
    (`Mailbox`) et le journal durable (`EventLog`) : mémoire pour les tests et un
    déploiement mono-process, Redis pour la production — et c'est Redis qui rend
    la lecture **indépendante du process qui a lancé le run**, sans quoi la
    troisième exigence du ticket serait hors d'atteinte.
    """

    @abstractmethod
    async def battre(self, run_id: str, *, horodatage: str | None = None) -> None:
        """Pose le battement de `run_id` (par défaut : maintenant)."""
        raise NotImplementedError

    @abstractmethod
    async def battements(self) -> dict[str, str]:
        """Tous les battements connus : `run_id → horodatage du dernier battement`."""
        raise NotImplementedError

    @abstractmethod
    async def oublier(self, run_id: str) -> None:
        """Retire le battement de `run_id` — sans effet s'il n'y en a pas."""
        raise NotImplementedError

    # Hook optionnel, pas un point du contrat : no-op assumé (d'où le noqa B027) —
    # seul un registre à connexions (Redis) a quelque chose à libérer. Même parti
    # pris que `EventLog.close`.
    async def close(self) -> None:  # noqa: B027
        """Libère les ressources du registre (connexions) — no-op par défaut."""


class RegistreBattementsMemoire(RegistreBattements):
    """Registre en mémoire : un dict en process, aucune visibilité inter-process.

    Le registre des **tests** et d'un déploiement mono-process. Il honore le
    contrat, mais un run publié par un autre process n'y bat pas : ses runs
    ressortent alors `indetermine`, ce qui est exactement ce qu'un registre qui
    n'entend rien doit dire. La visibilité inter-process exige
    `RegistreBattementsRedis`.
    """

    def __init__(self) -> None:
        self._battements: dict[str, str] = {}

    async def battre(self, run_id: str, *, horodatage: str | None = None) -> None:
        self._battements[run_id] = horodatage or horodatage_battement()

    async def battements(self) -> dict[str, str]:
        # Copie : la lecture d'une liste de runs itère pendant qu'un run en vol
        # peut battre.
        return dict(self._battements)

    async def oublier(self, run_id: str) -> None:
        self._battements.pop(run_id, None)


class RegistreBattementsRedis(RegistreBattements):
    """Registre adossé à un hash Redis — le chemin de production (#348).

    `HSET`/`HGETALL`/`HDEL` sur la clé `cle`, instance mutualisée avec le reste de
    la Control Tower. La connexion est paresseuse (construite ici, ouverte au
    premier appel), comme celle de `RedisEventLog`.
    """

    def __init__(self, url: str | None = None, *, cle: str = CLE_BATTEMENTS) -> None:
        # Import local : seule la branche Redis dépend du client (le registre
        # mémoire des tests n'en a pas besoin).
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.Redis.from_url(url or REDIS_URL_DEFAUT)
        self._cle = cle

    async def battre(self, run_id: str, *, horodatage: str | None = None) -> None:
        await self._client.hset(self._cle, run_id, horodatage or horodatage_battement())

    async def battements(self) -> dict[str, str]:
        bruts = await self._client.hgetall(self._cle)
        return {_texte(cle): _texte(valeur) for cle, valeur in bruts.items()}

    async def oublier(self, run_id: str) -> None:
        await self._client.hdel(self._cle, run_id)

    async def close(self) -> None:
        await self._client.aclose()


def _texte(valeur: bytes | str) -> str:
    """Décode ce que rend le client Redis (octets par défaut) — jamais de levée."""
    return valeur.decode("utf-8", "replace") if isinstance(valeur, bytes) else str(valeur)


def batteur_redis(
    url: str | None = None, *, cle: str = CLE_BATTEMENTS
) -> Callable[[str], None]:
    """Construit le poseur de battement **synchrone** — le pendant de `publieur_redis`.

    Client Redis synchrone, pour un hôte qui n'a pas de boucle asyncio à sa
    disposition : `maestro-run --publier` publie déjà ses étapes par un handler
    `logging`, donc en synchrone (cf. `maestro.controltower.bridge`). La connexion
    est paresseuse (ouverte au premier `HSET`).
    """
    # Import local : seule la publication Redis dépend du client.
    import redis

    client = redis.Redis.from_url(url or REDIS_URL_DEFAUT)

    def battre(run_id: str) -> None:
        client.hset(cle, run_id, horodatage_battement())

    return battre


def oublieur_redis(
    url: str | None = None, *, cle: str = CLE_BATTEMENTS
) -> Callable[[str], None]:
    """Construit l'effaceur de battement **synchrone** — le pendant de `batteur_redis` (#446).

    Même client, même clé, même paresse : ce qu'il manquait à un hôte sans boucle
    asyncio pour **finir** proprement. Tant que l'API était seule à solder un run,
    `RegistreBattements.oublier` suffisait ; depuis que l'hôte détaché est le
    défaut, chaque run laisserait sinon une entrée définitive dans un hash relu en
    entier (`HGETALL`) à chaque `GET /api/executions`.

    Le geste ne se juge pas ici : il n'a de sens qu'**après** une issue publiée, et
    c'est `maestro.controltower.bridge.solder_le_run` qui tient les deux ensemble
    (cf. l'en-tête du module). Appelé seul, il transformerait un orphelin en
    indéterminé, c'est-à-dire un verdict juste en absence d'information.
    """
    import redis

    client = redis.Redis.from_url(url or REDIS_URL_DEFAUT)

    def oublier(run_id: str) -> None:
        client.hdel(cle, run_id)

    return oublier


class CoeurRun:
    """Fait battre un run depuis un **fil démon** — l'hôte bat tant qu'il vit (#348).

    Les hôtes visés sont `maestro-run --publier` et le process détaché de #443 :
    des process synchrones, dont la boucle asyncio est ouverte et refermée par le
    moteur (`run_borne`) et n'appartient donc pas à l'appelant. Un fil démon est ce
    qui bat *à côté* du run sans rien lui demander — et « démon » est le point : un
    cœur ne doit jamais retenir un process qui veut sortir.

    `demarrer` bat **tout de suite** puis toutes les `periode` secondes ; c'est ce
    premier battement synchrone qui garantit qu'aucun run n'est lu `indetermine`
    entre son lancement et son premier tour d'horloge. `arreter` arrête le fil
    sans rien effacer : le dernier battement reste, il vieillit, et c'est lui qui
    fera le verdict (cf. l'en-tête du module).

    L'effacement, lui, n'est **pas** ici et ce n'est pas un oubli : arrêter de
    battre et solder un run sont deux faits différents, et les confondre ferait
    disparaître le signal des morts brutales, seules à mériter encore `orphelin`.
    Un hôte qui a une issue à publier passe par `solder_le_run` (#446), qui
    effacera son battement **après** l'avoir publiée.

    Un battement en échec (Redis injoignable) est **tracé et abandonné**, jamais
    levé : même politique que le pont télémétrie — un signal de vie ne doit pas
    faire échouer la vie qu'il signale.
    """

    def __init__(
        self,
        run_id: str,
        battre: Callable[[str], None],
        *,
        periode: float = PERIODE_BATTEMENT_S,
    ) -> None:
        self._run_id = run_id
        self._battre = battre
        self._periode = periode
        self._arret = threading.Event()
        self._fil: threading.Thread | None = None

    def demarrer(self) -> None:
        """Bat une première fois, puis ouvre le fil qui battra jusqu'à `arreter`."""
        if self._fil is not None:
            return
        self._battement()
        self._fil = threading.Thread(
            target=self._boucle, name=f"maestro-battement-{self._run_id}", daemon=True
        )
        self._fil.start()

    def arreter(self) -> None:
        """Arrête le fil — le dernier battement reste et se met à vieillir."""
        self._arret.set()
        fil, self._fil = self._fil, None
        if fil is not None:
            # Borné par la période : le fil dort sur l'événement d'arrêt, donc il
            # se réveille tout de suite ; la borne n'existe que pour ne jamais
            # suspendre une sortie de process sur un fil qui aurait déraillé.
            fil.join(timeout=self._periode)

    def _boucle(self) -> None:
        while not self._arret.wait(self._periode):
            self._battement()

    def _battement(self) -> None:
        try:
            self._battre(self._run_id)
        except Exception:
            _LOGGER.exception(
                "Battement du run %s impossible : le run continue, mais l'API "
                "finira par le croire orphelin.",
                self._run_id,
            )
