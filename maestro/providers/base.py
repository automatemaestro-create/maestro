"""Couche d'abstraction fournisseur — l'interface `ModelProvider` (ticket #32).

Cette frontière isole le *moteur d'agents* du *fournisseur d'IA* : le moteur ne
connaît que `ModelProvider` et un `ModelSpec` (`fournisseur + modèle`). Les
secrets d'authentification passent par le slot `Credentials`, point d'injection
défini ici et alimenté par le ticket #30 pour Claude.

Ajouter un fournisseur = sous-classer `ModelProvider` puis l'enregistrer dans le
registre (voir `maestro.providers.registry`) — sans toucher au moteur.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from maestro.deliberation import CreditArbitrage
from maestro.providers.arbitrage import Arbitre, ArbitreActe
from maestro.providers.blocage import Signaleur
from maestro.providers.courrier import Courrier

if TYPE_CHECKING:  # imports de typage seuls — pas de dépendance d'exécution vers agents
    from maestro.agents.mcp import ServeurMcp
    from maestro.agents.permissions import PolitiqueOutils
    from maestro.detail_tache import EtapeTache
    from maestro.projets.modele import Projet

#: Borne appliquée à une exécution agentique dont l'appelant n'en fixe pas — depuis
#: #494 il n'y en a plus : le défaut est **l'absence de borne**, et c'est un choix,
#: pas un oubli. Une borne posée « au cas où » tue en plein travail un run
#: qui allait aboutir : `TurnLimitReached` est un échec **non transitoire**, donc
#: jamais relancé (ENF-06), et ce qui n'était pas commité est perdu net. Le défaut
#: conservateur de #239 (40 tours, 120 pour le Designer) avait déjà dû être desserré
#: après un `error_max_turns` qui a coûté un livrable — tâche runbook coupée à 41
#: tours pour 0,80 $ dépensés en vain, docs/15 §4.3 : relever une borne au premier
#: échec observé, c'est constater qu'elle protégeait mal. Même leçon que #286
#: (budget) et #326 (timeout).
#:
#: Le **réglage survit** : `plafond_tours` reste sur `RoleProfile`, `AgentRuntime` et
#: `run_agent`, à poser explicitement par qui en veut un.
PLAFOND_TOURS_DEFAUT: int | None = None


class UnsupportedCapability(RuntimeError):
    """Levée quand un fournisseur ne sait pas honorer une capacité *optionnelle*.

    Le POC n'exige de tout fournisseur que `generate` (texte). L'exécution
    *agentique outillée* (`run_agent`) est une capacité **native de l'Agent SDK**
    (donc de Claude) : un fournisseur qui n'en dispose pas la refuse explicitement
    plutôt que de la simuler. Le moteur peut ainsi *tester* la capacité sans présumer
    du fournisseur.
    """


class TurnLimitReached(RuntimeError):
    """Levée quand une exécution agentique a épuisé son plafond de tours (#91).

    C'est le garde-fou anti-emballement des exécutions outillées (ex. `max_turns`
    de l'Agent SDK) : chaque fournisseur mue son signal natif en cette exception
    pour que le moteur le reconnaisse **sans présumer du fournisseur**. Échec non
    transitoire par nature — relancer reproduirait le même emballement — donc
    jamais relancé par la relance automatique (`maestro.engine.retry`, ENF-06).

    Le plafond n'étant plus le même pour tous les agents (#239), le message
    **nomme la borne effectivement appliquée** : un échec dit de quelle limite il
    parle, et la lecture d'un journal distingue l'agent qu'on a serré trop court
    de celui qui s'emballe vraiment.

    Elle **subsiste sans défaut** (#494) : aucun agent du dépôt n'est plus borné,
    mais un `plafond_tours` explicite en pose toujours un, un fournisseur tiers peut
    avoir sa propre borne, et #479 en fait une cause d'arrêt nommée à l'écran. Sans
    borne posée, la levée dit `max_turns` faute de chiffre à citer.
    """


class McpServerUnavailable(RuntimeError):
    """Levée quand un serveur MCP déclaré ne peut pas être monté (#104).

    Couvre les deux empêchements : une déclaration **non montable** (variable
    d'environnement référencée absente — `maestro.agents.mcp.resolus`) et un
    serveur **injoignable à l'ouverture de session** (échec de démarrage ou de
    connexion, authentification requise — constaté par le fournisseur). Le
    message nomme le serveur et la cause : c'est l'« erreur propre » du contrat,
    consignée au journal comme tout échec de tâche. Non transitoire par nature
    (configuration ou secret à corriger) — jamais relancée (ENF-06).
    """


#: Nombre de lignes de stderr conservées d'un CLI fournisseur en échec (#346) :
#: les **dernières**, celles qui portent la cause immédiate. Un stderr de CLI peut
#: faire des milliers de lignes ; le journal d'un run est relu à l'écran, pas archivé.
STDERR_LIGNES_MAX = 20

#: Longueur maximale d'une ligne conservée (#346), au-delà tronquée : un CLI en
#: détresse recrache volontiers un JSON de plusieurs milliers de caractères sur
#: une seule ligne, qui ferait à lui seul tout le « résumé ».
STDERR_LIGNE_MAX = 500

#: Ce que dit l'étape de journal quand le CLI n'a **rien** écrit sur stderr (#346).
#: C'est l'autre moitié du ticket : « Check stderr output for details » renvoyait à
#: un flux vide sans jamais dire s'il était vide ou seulement jamais lu — les deux
#: se ressemblent à la lecture, et un seul des deux se répare.
MENTION_STDERR_VIDE = "stderr du CLI : aucune ligne — le CLI n'a rien écrit."

#: Attribut par lequel une exception de fournisseur transporte le résumé du stderr
#: jusqu'au journal (#346). Un **attribut** et non un message enrichi : le type et
#: le texte de l'exception restent intacts, donc la classification transitoire /
#: non transitoire (`maestro.engine.retry.est_transitoire`) et les erreurs typées
#: de la frontière (`TurnLimitReached`…) ne changent pas de sens en chemin.
_ATTR_STDERR = "_maestro_stderr"

_E = TypeVar("_E", bound=BaseException)


class CollecteurStderr:
    """Collecte **bornée** du stderr d'un CLI de fournisseur (#346).

    Le Claude Agent SDK lance le CLI en sous-processus et n'expose son stderr que
    par un rappel ligne à ligne (`ClaudeAgentOptions.stderr`) : sans rappel, rien
    n'est capturé et l'exception du SDK renvoie à un flux qui n'existe nulle part
    (« Check stderr output for details »). Cet objet *est* ce rappel — il s'appelle
    comme une fonction — et garde les `lignes_max` **dernières** lignes, chacune
    tronquée à `ligne_max` caractères.

    Ce qui est conservé est donc borné des deux côtés, et c'est le garde-fou du
    ticket : un stderr volumineux ne noie pas le journal. Ce qui n'est **jamais**
    recopié, c'est l'environnement passé au CLI (`ClaudeAgentOptions.env`, qui
    porte les credentials) — seul ce que le CLI écrit lui-même passe ici.
    """

    def __init__(
        self, *, lignes_max: int = STDERR_LIGNES_MAX, ligne_max: int = STDERR_LIGNE_MAX
    ) -> None:
        self._lignes: deque[str] = deque(maxlen=max(1, lignes_max))
        self._ligne_max = max(1, ligne_max)
        self._vues = 0

    def __call__(self, ligne: str) -> None:
        """Rappel du SDK — une ligne de stderr. Ne lève jamais : observer ne casse rien."""
        texte = str(ligne).rstrip("\r\n")
        if not texte.strip():
            return
        self._vues += 1
        if len(texte) > self._ligne_max:
            texte = texte[: self._ligne_max] + " […]"
        self._lignes.append(texte)

    @property
    def lignes(self) -> tuple[str, ...]:
        """Les dernières lignes retenues, dans l'ordre où le CLI les a écrites."""
        return tuple(self._lignes)

    def resume(self) -> str:
        """Le texte à consigner : les lignes retenues, **ou** la mention d'un flux vide.

        Toujours non vide — c'est le contrat : une étape de journal qui ne dit rien
        du stderr est exactement ce que le ticket #346 est venu supprimer.
        """
        if not self._lignes:
            return MENTION_STDERR_VIDE
        omises = self._vues - len(self._lignes)
        entete = "stderr du CLI"
        if omises > 0:
            entete += f" (dernières lignes ; {omises} antérieure(s) omise(s))"
        return entete + " :\n" + "\n".join(self._lignes)


def attache_stderr(exc: _E, resume: str) -> _E:
    """Accroche `resume` à `exc` et rend l'exception, pour un `raise` en une ligne.

    Best-effort : une exception qui refuse l'attribut (`__slots__`) repart telle
    quelle plutôt que d'échouer — tracer une cause ne doit jamais en créer une.
    """
    try:
        setattr(exc, _ATTR_STDERR, resume)
    except Exception:  # noqa: BLE001 — l'observation ne casse jamais l'observé
        pass
    return exc


def stderr_de(exc: BaseException) -> str | None:
    """Le résumé de stderr accroché à `exc`, ou `None` si personne n'en a collecté.

    `None` et `MENTION_STDERR_VIDE` ne disent pas la même chose : le premier est
    « ce fournisseur ne capture pas le stderr » (aucun CLI, ou pas encore câblé),
    le second « le CLI a été écouté et n'a rien dit ».
    """
    valeur = getattr(exc, _ATTR_STDERR, None)
    return valeur if isinstance(valeur, str) and valeur else None


class AuthMode(StrEnum):
    """Mode d'authentification d'un fournisseur — la « bascule » du ticket #30.

    - ``SUBSCRIPTION`` (défaut) : authentification par **abonnement** (OAuth), sans
      clé API. Pour Claude, c'est l'abonnement Claude Code — point de départ du POC.
    - ``API_KEY`` : authentification par **clé API** (facturation à l'usage).
    """

    API_KEY = "api_key"
    SUBSCRIPTION = "subscription"


@dataclass(frozen=True)
class Credentials:
    """Point d'injection des secrets d'authentification d'un fournisseur.

    #32 a défini le *slot* (la frontière) ; #30 y branche la **bascule** entre les
    deux `AuthMode` et la règle de précédence (côté config → cf.
    `claude.ClaudeProvider.from_settings`). Le mode `SUBSCRIPTION` ne requiert
    aucune clé : le fournisseur Claude s'authentifie alors via l'abonnement Claude
    Code (OAuth de l'Agent SDK) ou un `oauth_token` explicite (utile en CI). Le
    mode `API_KEY` exige `api_key` — l'invariant est vérifié à la construction.
    """

    auth_mode: AuthMode = AuthMode.SUBSCRIPTION
    api_key: str | None = None
    oauth_token: str | None = None

    def __post_init__(self) -> None:
        if self.auth_mode is AuthMode.API_KEY and not self.api_key:
            raise ValueError(
                "Mode d'authentification 'api_key' sans clé : renseignez `api_key`, "
                "ou choisissez le mode 'subscription'."
            )


@dataclass(frozen=True)
class ModelSpec:
    """Sélection `fournisseur + modèle` d'un agent (partie versionnable du contrat).

    Volontairement dépourvu de secret : un `ModelSpec` peut être stocké et
    versionné (cf. playbooks en base) sans jamais transporter de credentials —
    ceux-ci passent par `Credentials`, injecté séparément.
    """

    provider: str
    model: str


@dataclass(frozen=True)
class ModeleDisponible:
    """Un modèle qu'un fournisseur annonce servir, et les efforts qu'il y admet (#253).

    C'est la fiche que le catalogue rend à l'UI : `nom` est l'identifiant passé
    à `ModelSpec.model` (la chaîne exacte que le fournisseur attend), `libelle`
    le nom lisible, `efforts` les niveaux d'effort **admis sur ce modèle**.

    `efforts` est **vide quand le fournisseur n'expose pas ce réglage**, et c'est
    une réponse à part entière : elle dit « ce modèle ne se règle pas en effort »,
    pas « on ne sait pas ». La granularité est le modèle et non le fournisseur
    parce que rien ne garantit qu'un fournisseur règle de la même façon toute sa
    gamme — un fournisseur dont tous les modèles partagent la liste la répète, ce
    qui ne coûte rien et n'oblige personne à défaire une structure le jour où ce
    ne sera plus vrai.
    """

    nom: str
    libelle: str = ""
    efforts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Réémet la fiche en dict JSON-sérialisable (forme servie par l'API)."""
        return {
            "nom": self.nom,
            "libelle": self.libelle or self.nom,
            "efforts": list(self.efforts),
        }


@dataclass(frozen=True)
class FournisseurDisponible:
    """Un fournisseur du registre, tel que le catalogue le rend (#253).

    `modeles` est la gamme **annoncée** ; `modeles_libres` dit qu'un nom hors de
    cette gamme reste recevable. Les deux ensemble sont ce qui rend la liste vide
    lisible : `openai` fédère des endpoints aux nommages hétéroclites (`gpt-*`,
    `llama3:8b`, `org/modele`) et ne peut en préjuger — sa gamme est vide **et**
    libre, ce qui veut dire « saisis le nom », jamais « aucun modèle ». Un
    fournisseur à gamme vide et non libre, lui, n'a rien à proposer.
    """

    nom: str
    modeles: tuple[ModeleDisponible, ...] = ()
    modeles_libres: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Réémet la fiche en dict JSON-sérialisable (forme servie par l'API)."""
        return {
            "nom": self.nom,
            "modeles": [m.to_dict() for m in self.modeles],
            "modeles_libres": self.modeles_libres,
        }


class ModelProvider(ABC):
    """Frontière entre le moteur d'agents et un fournisseur d'IA.

    Le moteur ne dépend que de cette interface : il résout un fournisseur par son
    nom (`ModelSpec.provider`), lui confie un modèle + un prompt, et récupère le
    texte de la réponse. Sont câblés : Claude (`claude.ClaudeProvider`) et tout
    endpoint compatible OpenAI (`openai_compat.OpenAICompatProvider`, #69).
    """

    #: Nom stable du fournisseur, tel que référencé par `ModelSpec.provider`.
    name: ClassVar[str]

    #: Gamme annoncée par le fournisseur (#253) : ce que le catalogue rend à l'UI,
    #: et **la seule source** de la liste des modèles — un fournisseur ajouté au
    #: registre apparaît sans qu'aucune liste ne soit recopiée ailleurs. Déclarée
    #: sur la **classe** et non sur l'instance : la lire ne doit demander ni
    #: credentials, ni réseau, ni configuration (le catalogue répond avant qu'un
    #: seul fournisseur soit construit).
    MODELES: ClassVar[tuple[ModeleDisponible, ...]] = ()

    #: Un nom de modèle hors de `MODELES` est-il recevable (#253) ? Vrai pour un
    #: adaptateur qui fédère des endpoints dont il ne peut pas préjuger — c'est
    #: `supports()` qui tranche à l'exécution, cet indicateur ne fait que le dire
    #: d'avance à l'UI. Faux par défaut : annoncer une gamme, c'est s'y tenir.
    MODELES_LIBRES: ClassVar[bool] = False

    @classmethod
    def catalogue(cls) -> FournisseurDisponible:
        """La fiche de ce fournisseur pour le catalogue (#253) — sans rien construire."""
        return FournisseurDisponible(
            nom=cls.name, modeles=cls.MODELES, modeles_libres=cls.MODELES_LIBRES
        )

    @classmethod
    def efforts_admis(cls, model: str) -> tuple[str, ...]:
        """Les niveaux d'effort que ce fournisseur admet sur `model` (vide : aucun).

        Vide aussi pour un modèle **hors gamme** — on ne sait rien de ce qu'il
        admet, et supposer serait le seul moyen d'envoyer un réglage qu'un
        endpoint refuserait.
        """
        for modele in cls.MODELES:
            if modele.nom == model:
                return modele.efforts
        return ()

    @classmethod
    def effort_admis(cls, model: str, effort: str | None) -> str | None:
        """`effort` s'il est admis sur `model`, sinon `None` — le **filtre unique** (#253).

        C'est ici, et nulle part ailleurs, que se décide « ce fournisseur
        connaît-il cet effort ? ». Ses deux appelants — le runtime outillé et
        l'exécuteur texte — ne transmettent le réglage au fournisseur **que**
        lorsque ce verbe rend une valeur : un fournisseur qui n'expose aucun
        effort n'en reçoit donc jamais, et l'ignorer proprement ne dépend pas de
        sa bonne volonté mais de la construction. Un effort inconnu (valeur
        obsolète restée sur la définition d'un agent, modèle changé depuis) suit
        exactement le même chemin : il est écarté, sans erreur — le réglage est
        un **conseil**, jamais une condition d'exécution.
        """
        if not effort:
            return None
        return effort if effort in cls.efforts_admis(model) else None

    @abstractmethod
    def supports(self, model: str) -> bool:
        """Le fournisseur sait-il servir ce modèle ?"""
        raise NotImplementedError

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        effort: str | None = None,
    ) -> str:
        """Exécute un appel modèle et renvoie le texte assemblé de la réponse.

        La couture minimale du POC, et le seul appel que tout fournisseur doit
        savoir servir. Le **streaming** est venu s'ajouter à côté d'elle
        (`generate_stream`, #693) plutôt qu'à sa place : rendre le texte d'un
        bloc reste la façon normale de demander une réponse dont on n'a rien à
        faire avant qu'elle soit entière.

        `effort` (#253) est le niveau d'effort demandé au modèle, tel qu'il est
        porté par la définition de l'agent. Il n'arrive ici **que s'il est
        admis** — l'appelant le passe au tamis de `effort_admis`, qui est le seul
        endroit où la question se tranche — si bien qu'un fournisseur sans
        `MODELES` n'en voit jamais un seul. C'est aussi pourquoi il porte un
        défaut : un fournisseur tiers qui l'ignore reste conforme sans changer
        une ligne, et le réglage n'est jamais une condition d'exécution.
        """
        raise NotImplementedError

    async def generate_stream(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[str]:
        """Le même appel que `generate`, rendu **par incréments** (#693).

        La frontière expose ici ce que `generate` ne pouvait pas dire : une
        réponse qui s'écrit. L'appelant reçoit les morceaux dans l'ordre où le
        fournisseur les produit, et **la concaténation des morceaux est
        exactement ce que `generate` aurait rendu** — c'est le seul invariant du
        canal, et celui dont dépend le contrat de la trame `fin` du flux de chat
        (`maestro.controltower.chat`, docs/05 §6.5) : un client qui recolle les
        `delta` doit retomber sur le message complet, sans un caractère de plus
        ni de moins, sans réordonnancement.

        **Capacité optionnelle, et honorée par tous** — c'est ce qui la distingue
        de `run_agent`, qui se refuse (`UnsupportedCapability`) quand un
        fournisseur ne sait pas l'exécuter. Ici l'implémentation par défaut *est*
        une réponse valide : elle appelle `generate` et rend le texte entier en
        **un seul** morceau. Un fournisseur qui ne sait pas streamer continue donc
        de fonctionner sans être modifié, et l'appelant n'a aucune capacité à
        tester avant d'appeler — il consomme des incréments, il y en a un ou
        cent. C'est le pendant exact, à l'étage fournisseur, de ce que
        `RepondeurChat.produire` fait à l'étage répondeur.

        Un texte vide ne rend **aucun** morceau plutôt qu'un morceau vide : « le
        modèle n'a rien dit » se lit à l'absence d'incréments, et non à un
        fragment qui ferait croire à un début de réponse.

        Un fournisseur qui **échoue en cours de flux** lève, comme `generate` :
        les morceaux déjà rendus l'ont été, et c'est à l'appelant de décider ce
        qu'il en fait — les publier puis se taire est précisément ce que le canal
        de chat interdit (`Redaction.interruption`, #693). La frontière ne
        rattrape rien et ne rejoue rien : elle ne sait pas si ce qui est déjà
        parti a été montré à quelqu'un.
        """
        # Le mot-clé ne voyage que s'il a quelque chose à dire (#253) : sans
        # effort demandé, l'appel est **au bit près** celui d'avant ce lot, et un
        # fournisseur qui n'a pas surchargé `generate` avec ce paramètre continue
        # de streamer sans changer une ligne.
        reglage = {"effort": effort} if effort else {}
        texte = await self.generate(
            prompt, model=model, system_prompt=system_prompt, **reglage
        )
        if texte:
            yield texte

    async def run_agent(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        workspace: Path,
        tools: Sequence[str],
        mcp_serveurs: Sequence[ServeurMcp] = (),
        politique: PolitiqueOutils | None = None,
        on_refus: Callable[[str, str], None] | None = None,
        on_arbitrage_acte: ArbitreActe | None = None,
        on_activite: Callable[[str], None] | None = None,
        on_etapes: Callable[[Sequence[EtapeTache]], None] | None = None,
        on_arbitrage: Arbitre | None = None,
        on_blocage: Signaleur | None = None,
        credit_arbitrage: CreditArbitrage | None = None,
        on_courrier: Courrier | None = None,
        plafond_tours: int | None = PLAFOND_TOURS_DEFAUT,
        projet: Projet | None = None,
        effort: str | None = None,
    ) -> str:
        """Exécution *agentique outillée* : renvoie le compte-rendu final de l'agent.

        Le modèle dispose des outils `tools` (fichiers, shell…) et travaille dans
        `workspace`, son **répertoire de travail isolé** (cf. `maestro.sandbox`), où
        il produit un livrable concret (des fichiers). Là où `generate` rend du texte,
        `run_agent` *agit* dans un espace dédié.

        `projet` (#226) est le projet dans lequel la tâche travaille — `workspace`
        est alors l'espace **dérivé** de ce projet (#224 : worktree ou copie), et
        non plus un répertoire jetable. Un fournisseur qui **isole** l'exécution
        s'en sert pour monter cet espace sans jamais monter la racine du projet,
        et pour que les exclusions du périmètre tiennent jusque dans le conteneur
        (`maestro.sandbox.container`). Les autres n'ont rien à en faire :
        l'exécution voit le même `workspace` dans les deux cas.

        `mcp_serveurs` (#104) sont les serveurs MCP déclarés par l'agent, **déjà
        résolus** (`maestro.agents.mcp.resolus` — plus aucune référence
        `${VAR}`) : un fournisseur qui honore la capacité les monte sur la
        session (et rien d'autre — aucune configuration MCP ambiante), ou lève
        `McpServerUnavailable` si l'un d'eux est injoignable. La déclaration
        reste agnostique : la traduction vers le format natif vit ici, jamais
        dans la logique d'agent.

        `politique` (#110) est la politique allow/deny de l'agent, **déjà
        appliquée en amont** au montage (outils filtrés, serveurs MCP refusés
        non montés) : le fournisseur qui la reçoit doit en plus **refuser au
        vol** tout appel d'outil qu'elle interdit (ex. un outil MCP refusé
        individuellement sur un serveur monté) — refus propre servi au modèle,
        qui poursuit sa tâche ; la violation n'est jamais fatale au run. Chaque
        refus est signalé via `on_refus(outil, raison)` quand il est fourni —
        c'est le canal de traçage de l'appelant (journal, fil temps réel) ; un
        échec du callback ne doit jamais casser l'exécution observée.

        `on_arbitrage_acte` (#583) est le canal du **troisième cran** de cette
        même politique (`ask`, #580) : un appel qu'elle soumet à arbitrage est
        **suspendu**, l'outil et ses arguments partent sur ce canal, et l'issue
        décide — `(True, détail)` laisse l'appel passer, `(False, détail)` le
        refuse avec son motif. C'est l'appelant qui compose la demande et la
        soumet au validateur configuré : le fournisseur ne connaît ni la tâche,
        ni le run, ni qui tranche.

        Deux exigences pèsent sur le fournisseur qui l'honore, et aucune n'est
        négociable. Il doit **refuser** un appel à arbitrer quand le canal est
        absent ou en panne — jamais l'approuver par défaut (EF-08, ENF-04) — et
        il doit **borner son attente sous la borne de son propre runtime**, de
        sorte que l'issue vienne toujours de lui et jamais d'une échéance. Un
        runtime qui borne la durée d'un point de contrôle (c'est le cas du CLI
        Claude Code, 60 s par défaut) déciderait sinon du sort d'un acte sensible
        par un comportement qui n'est pas le nôtre. À l'expiration, le
        fournisseur rend un refus **motivé par l'attente** — la demande, elle,
        reste en vol.

        ⚠ Ce canal-ci et `on_arbitrage` (#582, plus bas) aboutissent au même
        validateur mais ne transportent pas la même chose, et ce n'est pas un
        doublon : l'un porte **l'acte qu'on a intercepté**, l'autre **la raison
        qu'un agent a rédigée**. Le second est un canal *de plus*, dont le
        silence ne dispense de rien (cadrage du parent #573).

        `on_activite` (#479) est le canal de « ce que l'agent fait **pendant**
        qu'il le fait » : le fournisseur l'appelle avec une ligne déjà composée
        (`maestro.providers.activite`) chaque fois qu'il a quelque chose à dire —
        appel d'outil et sa cible, jalon de texte — **pendant** l'exécution, et
        non à son issue. Sans lui, une tâche de dix minutes produit dix minutes
        de silence : le moteur ne consigne que le début et la fin, et rien entre
        les deux, quelle que soit la durée.

        Deux exigences pèsent sur le fournisseur qui l'honore, et elles ne sont
        pas symétriques. Il doit signaler **chaque occurrence**, sans
        déduplication : savoir *quels* outils ont servi, mais ni combien de fois
        ni dans quel ordre, ne dit pas ce qu'un agent est en train de faire. Il
        doit en revanche **borner le débit** de ce qu'il publie — un agent
        outillé émet vite, et republier tout tel quel noierait le bus comme le
        flot d'une ligne par outil noyait la console du pilote (#240). Les deux
        tiennent ensemble parce que le regroupement se fait **après**
        l'observation, jamais à sa place : `RegulateurActivite` voit tous les
        gestes et n'en publie qu'un compte rendu périodique, qui annonce son
        propre regroupement.

        Même règle que `on_refus` sur les échecs : un callback qui lève ne doit
        jamais casser l'exécution observée.

        `on_etapes` (#489) est le canal de la **checklist** de la tâche : le
        fournisseur l'appelle avec l'état complet de la liste de travail de
        l'agent, tel qu'il vient de l'observer, chaque fois que celui-ci la pose
        ou la met à jour. L'état **complet** et non un delta, à dessein — c'est
        `maestro.detail_tache.SuiviChecklist` qui décide de ce qui progresse, et
        lui confier des deltas l'obligerait à reconstituer un état que le
        fournisseur a déjà sous les yeux.

        Capacité **optionnelle au second degré** : un fournisseur peut honorer
        `run_agent` sans jamais appeler ce canal, s'il n'a pas d'endroit où
        observer une checklist. La tâche reste alors exactement ce qu'elle est
        sans lui — pas de checklist vide, pas de bloc qui promette un contenu
        absent (règle de #246). C'est ce qui permet au couplage à l'outil
        d'exister d'un seul côté (`maestro.providers.checklist`) sans remonter
        jusqu'au moteur.

        Même règle que les deux autres canaux sur les échecs : un callback qui
        lève ne casse jamais l'exécution observée.

        `on_arbitrage` (#582, `maestro.providers.arbitrage`) est le seul canal
        des quatre qui aille **dans l'autre sens** : les trois précédents
        rapportent ce que le fournisseur observe, celui-ci porte une question de
        l'agent et rapporte la réponse. Un fournisseur qui l'honore expose à
        l'agent un outil `demander_arbitrage(raison)`, appelle ce canal quand
        l'agent s'en sert, **attend** la décision et la lui rend — approuvée, il
        poursuit ; refusée, il reçoit un motif exploitable et poursuit sans
        l'action. Capacité optionnelle au second degré, comme `on_etapes` : un
        fournisseur sans outillage n'expose rien et le moteur ne s'en aperçoit
        pas.

        L'exigence n'est pas non plus la même : ici, un callback qui lève ne
        peut pas être avalé en silence — le fournisseur doit rendre à l'agent un
        **refus** motivé (`maestro.providers.arbitrage.CANAL_EN_ERREUR`). Une
        panne d'observation ne coûte qu'une ligne de journal ; une panne du
        canal de décision laisserait l'agent sans réponse devant l'action même
        qu'il jugeait irréversible.

        `on_blocage` (#719, `maestro.providers.blocage`) part lui aussi **de**
        l'agent, et c'est le seul des cinq qui n'en revienne pas : un fournisseur
        qui l'honore expose un outil `signaler_blocage(raison)`, appelle ce canal
        quand l'agent s'en sert, et **rend la main immédiatement**. Rien n'est
        soumis à personne, rien n'est attendu, l'agent n'est jamais suspendu — un
        verbe qui attendrait serait un troisième canal d'arbitrage à tenir
        d'accord avec les deux autres (docs/31 §3.1), et c'est exactement ce que
        la signature synchrone de `Signaleur` rend inexprimable.

        Il **n'a donc pas de fenêtre de crédit** (`credit_arbitrage` ci-dessous)
        et n'en aura jamais : on ne mesure que les attentes, et celle-ci n'existe
        pas. Capacité optionnelle au second degré, comme `on_etapes` et
        `on_arbitrage`.

        L'exigence sur l'échec est celle du canal d'arbitrage et non celle des
        canaux d'observation, pour une raison qui leur est commune : l'agent
        **attend un accusé**, même s'il n'attend pas de réponse. Un callback qui
        lève ne remonte donc pas (il tuerait la tâche à l'instant où l'agent
        coopère) mais ne se tait pas non plus — le fournisseur lui dit que sa
        raison **n'a pas** été consignée
        (`maestro.providers.blocage.CANAL_EN_ERREUR`), faute de quoi il la croit
        transmise et ne la répète pas dans son compte-rendu final, seul endroit
        qui lui reste.

        `credit_arbitrage` (#584, `maestro.deliberation`) est le seul canal qui
        ne transporte ni observation ni décision : il transporte du **temps**. Le
        fournisseur qui honore l'un ou l'autre des deux canaux d'arbitrage
        ci-dessus **ouvre une fenêtre** (`credit.attente()`) autour de l'attente
        elle-même, et rien d'autre. L'appelant, lui, en déduit ce temps du délai
        qu'il a posé sur la tâche : un `timeout_s` ne tue jamais une tâche
        suspendue à une question adressée à un humain.

        C'est le fournisseur qui mesure parce qu'il est le seul à savoir, et
        l'écart n'est pas un détail de précision : il **cesse d'attendre** à sa
        propre borne pendant que la demande reste en vol (voir plus haut). Une
        mesure prise là où la demande est composée — dans le moteur — continuerait
        donc de courir pendant que l'agent a déjà repris son travail, et rendrait
        à la tâche du délai qu'elle a passé à travailler.

        Capacité optionnelle : sans ce canal, l'arbitrage fonctionne exactement
        comme avant et son temps reste compté dans celui de la tâche.

        `on_courrier` (#720, `maestro.providers.courrier`) est un canal de plus
        qui va **vers** l'agent : un fournisseur qui l'honore lui expose un outil
        `ecrire_a_un_pair(destinataire, message)` et appelle ce canal quand
        l'agent s'en sert. Il ne transporte **rien en retour** — l'appelant
        consigne le mot au journal du run et le publie en best-effort sur la
        boîte du destinataire, puis rend la main : l'agent n'attend pas, et
        aucune décision ne lui est due.

        Deux exigences, et elles ne ressemblent à aucune des précédentes. Le
        fournisseur doit **refuser lui-même** ce qui n'est pas adressable — un
        destinataire vide vaut *diffusion* côté transport, et l'identité de la
        boucle n'est pas un pair — parce que ces refus se répondent à l'agent et
        non à l'appelant. Et un callback qui lève **ne peut pas être avalé en
        silence** : la seule promesse de ce verbe étant l'écriture, son échec est
        la seule nouvelle qui change quelque chose pour l'agent
        (`maestro.providers.courrier.CANAL_EN_ERREUR`) — mais il ne doit pas
        pour autant tuer la tâche.

        Ce qui est promis à l'agent est une **trace adressée**, jamais une
        livraison : le transport est un pub/sub éphémère (pas de rejeu, abonné
        requis avant publication) et un agent n'existe que pendant sa tâche. Un
        fournisseur qui expose ce verbe doit donc le présenter ainsi — c'est la
        réserve de docs/31 §3.2, et elle est dans la description de l'outil.

        Capacité optionnelle au second degré, comme `on_etapes` et
        `on_arbitrage` : un fournisseur sans outillage n'expose rien et le moteur
        ne s'en aperçoit pas.

        `plafond_tours` (#239) borne la boucle agentique — dépassé ⇒
        `TurnLimitReached`. Il est **fourni par l'appelant** (le profil de
        l'agent, via `AgentRuntime`) et non lu dans une constante du fournisseur :
        un tour n'a pas de coût stable d'un rôle à l'autre (facteur 7 mesuré entre
        une tâche de validation et une tâche de conception), donc une borne unique
        protège mal les uns en bridant les autres. Un appel qui n'en fournit pas
        n'est **pas borné** (#494, `PLAFOND_TOURS_DEFAUT` valant `None`) : le
        défaut est l'absence de borne, et un fournisseur ne doit donc pas en
        inventer une — celui qui en veut une la reçoit.

        `effort` (#253) est le niveau d'effort demandé au modèle, porté par la
        définition de l'agent et passé par le runtime. Comme sur `generate`, il
        n'arrive ici **que s'il est admis** (`effort_admis`), et le runtime ne
        transmet même pas le mot-clé quand il n'y a rien à transmettre : un
        fournisseur outillé qui ne connaît pas ce réglage — le nôtre d'hier, ou
        celui d'un tiers — n'a rien à changer pour rester conforme.

        Capacité **optionnelle** : la base la refuse (`UnsupportedCapability`) ; un
        fournisseur outillé (Claude via l'Agent SDK) la surcharge. Le moteur reste
        agnostique — il teste la capacité, il ne présume pas du fournisseur.
        """
        raise UnsupportedCapability(
            f"Le fournisseur {self.name!r} n'expose pas d'exécution agentique outillée."
        )
