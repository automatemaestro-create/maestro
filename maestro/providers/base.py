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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, TypeVar

from maestro.deliberation import CreditArbitrage
from maestro.providers.arbitrage import Arbitre, ArbitreActe

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


class ModelProvider(ABC):
    """Frontière entre le moteur d'agents et un fournisseur d'IA.

    Le moteur ne dépend que de cette interface : il résout un fournisseur par son
    nom (`ModelSpec.provider`), lui confie un modèle + un prompt, et récupère le
    texte de la réponse. Sont câblés : Claude (`claude.ClaudeProvider`) et tout
    endpoint compatible OpenAI (`openai_compat.OpenAICompatProvider`, #69).
    """

    #: Nom stable du fournisseur, tel que référencé par `ModelSpec.provider`.
    name: ClassVar[str]

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
    ) -> str:
        """Exécute un appel modèle et renvoie le texte assemblé de la réponse.

        C'est la couture minimale du POC ; elle grandira (streaming, outils,
        sous-agents) sans changer la nature de la frontière.
        """
        raise NotImplementedError

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
        credit_arbitrage: CreditArbitrage | None = None,
        plafond_tours: int | None = PLAFOND_TOURS_DEFAUT,
        projet: Projet | None = None,
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

        `plafond_tours` (#239) borne la boucle agentique — dépassé ⇒
        `TurnLimitReached`. Il est **fourni par l'appelant** (le profil de
        l'agent, via `AgentRuntime`) et non lu dans une constante du fournisseur :
        un tour n'a pas de coût stable d'un rôle à l'autre (facteur 7 mesuré entre
        une tâche de validation et une tâche de conception), donc une borne unique
        protège mal les uns en bridant les autres. Un appel qui n'en fournit pas
        n'est **pas borné** (#494, `PLAFOND_TOURS_DEFAUT` valant `None`) : le
        défaut est l'absence de borne, et un fournisseur ne doit donc pas en
        inventer une — celui qui en veut une la reçoit.

        Capacité **optionnelle** : la base la refuse (`UnsupportedCapability`) ; un
        fournisseur outillé (Claude via l'Agent SDK) la surcharge. Le moteur reste
        agnostique — il teste la capacité, il ne présume pas du fournisseur.
        """
        raise UnsupportedCapability(
            f"Le fournisseur {self.name!r} n'expose pas d'exécution agentique outillée."
        )
