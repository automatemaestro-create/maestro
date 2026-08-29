"""Fournisseur Claude — le fournisseur historique du POC (tickets #32 puis #30).

Adapte l'interface `ModelProvider` au **Claude Agent SDK** (`claude_agent_sdk`),
runtime des agents Claude. #30 y branche les **deux modes d'authentification** —
clé API ou abonnement Claude Code — sélectionnables par configuration, via le seul
slot `Credentials` (pas de mécanisme d'auth parallèle).

Le SDK lance le CLI Claude Code en sous-processus ; on pilote donc l'auth par les
variables d'environnement que le CLI reconnaît, injectées via `ClaudeAgentOptions.env`.
Ordre de précédence du CLI (du plus fort au plus faible) utilisé ici :

    2. ANTHROPIC_AUTH_TOKEN  (bearer, passerelles)
    3. ANTHROPIC_API_KEY     (clé API ; en mode non-interactif — celui du SDK —
                              toujours utilisée si présente)
    5. CLAUDE_CODE_OAUTH_TOKEN (token d'abonnement longue durée, CI)
    6. abonnement Claude Code via `claude` (défaut Pro/Max)

`options.env` étant *fusionné par-dessus* l'environnement hérité, il ne permet pas
de *supprimer* une variable : on neutralise donc explicitement les concurrents de
rang supérieur en les mettant à la chaîne vide (traitée comme « non définie » par
le CLI — vérifié), afin que le mode choisi l'emporte quel que soit l'environnement
ambiant.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookJSONOutput,
    HookMatcher,
    McpServerConfig,
    McpServerStatus,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from claude_agent_sdk.types import HookInput

from maestro.acte import arguments_depuis
from maestro.config import ConfigError, Settings
from maestro.decideur import Decideur
from maestro.deliberation import CreditArbitrage
from maestro.detail_tache import EtapeTache
from maestro.providers.activite import Geste, RegulateurActivite
from maestro.providers.arbitrage import (
    CANAL_EN_ERREUR,
    DESCRIPTION_OUTIL,
    NOM_OUTIL,
    NOM_SERVEUR,
    RAISON_MANQUANTE,
    SCHEMA_ENTREE,
    Arbitre,
    ArbitreActe,
    BornesArbitrage,
    motif_approbation,
    motif_attente,
    motif_auto,
    motif_panne,
    motif_refus,
    motif_sans_arbitre,
    reponse,
)
from maestro.providers.base import (
    PLAFOND_TOURS_DEFAUT,
    AuthMode,
    CollecteurStderr,
    Credentials,
    McpServerUnavailable,
    ModelProvider,
    TurnLimitReached,
    attache_stderr,
)
from maestro.providers.checklist import est_checklist, etapes_depuis_outil
from maestro.sandbox.container import IsolationConfig

if TYPE_CHECKING:  # imports de typage seuls — pas de dépendance d'exécution vers agents
    from maestro.agents.mcp import ServeurMcp
    from maestro.agents.permissions import PolitiqueOutils
    from maestro.projets.modele import Projet
from maestro.providers.registry import register
from maestro.telemetry import StepUsage, report_usage

#: Marqueur du plafond de tours dans les erreurs du SDK : le CLI rend un résultat
#: `is_error=True` de sous-type `error_max_turns`, que le SDK relève en exception
#: « Claude Code returned an error result: error_max_turns ».
_MARQUEUR_MAX_TURNS = "error_max_turns"

#: États d'un serveur MCP qui valent échec définitif (statut `get_mcp_status`) :
#: démarrage/connexion en échec, authentification requise, serveur désactivé.
#: « pending » n'en fait pas partie : c'est l'état transitoire du démarrage
#: (npx qui télécharge, endpoint lent), attendu jusqu'à `_MCP_CONNEXION_MAX_S`.
_MCP_STATUTS_ECHEC = frozenset({"failed", "needs-auth", "disabled"})

#: Délai maximal accordé à la connexion des serveurs MCP déclarés avant l'échec
#: propre (`McpServerUnavailable`). Large : le premier `npx -y` d'un serveur
#: télécharge son paquet. Le time-out par tâche du moteur (#64) borne le tout.
_MCP_CONNEXION_MAX_S: float = 60.0

#: Période du sondage de statut pendant l'attente de connexion des serveurs MCP.
_MCP_SONDAGE_S: float = 0.5

_E = TypeVar("_E", bound=BaseException)


def _resolve_auth_mode(settings: Settings) -> AuthMode:
    """Applique la règle de précédence pour déterminer le mode d'authentification.

    1. `CLAUDE_AUTH_MODE` explicite (`api_key`/`subscription`) l'emporte.
    2. Sinon, déduction : une clé API présente ⇒ `api_key` ; sinon `subscription`
       (défaut du POC, où l'on part de l'abonnement Claude Code).
    """
    raw = settings.claude_auth_mode
    if raw:
        try:
            return AuthMode(raw)
        except ValueError as exc:
            valid = ", ".join(m.value for m in AuthMode)
            raise ConfigError(
                f"CLAUDE_AUTH_MODE invalide : {raw!r}. Valeurs acceptées : {valid}."
            ) from exc
    return AuthMode.API_KEY if settings.anthropic_api_key else AuthMode.SUBSCRIPTION


class ClaudeProvider(ModelProvider):
    """Runtime des agents Claude, derrière la couche d'abstraction."""

    name: ClassVar[str] = "claude"

    #: Préfixe des identifiants de modèles Claude (ex. `claude-opus-5`).
    _MODEL_PREFIX: ClassVar[str] = "claude-"

    def __init__(
        self,
        credentials: Credentials,
        *,
        isolation: IsolationConfig | None = None,
        arbitrage: BornesArbitrage | None = None,
    ) -> None:
        self._credentials = credentials
        # Mode isolé (#108) : quand il est actif, `run_agent` lance le CLI dans un
        # conteneur durci via le shim (`cli_path`). None : exécution sur l'hôte
        # (comportement historique, défaut).
        self._isolation = isolation
        # Bornes de l'arbitrage au vol (#583) : ce qu'on laisse à la personne qui
        # tranche, et ce qu'on annonce au runtime comme durée max du hook. None :
        # les défauts du module — jamais ceux du SDK, qu'on ne choisit pas.
        self._arbitrage = arbitrage or BornesArbitrage()

    @property
    def credentials(self) -> Credentials:
        """Credentials injectés à la construction (slot alimenté par #30)."""
        return self._credentials

    @classmethod
    def from_settings(cls, settings: Settings) -> ClaudeProvider:
        """Construit le fournisseur en dérivant credentials et isolation de la config.

        Applique la bascule des modes (`_resolve_auth_mode`) puis valide le mode
        retenu : `api_key` exige `ANTHROPIC_API_KEY` ; `subscription` n'exige rien
        (auth par abonnement Claude Code, ou `CLAUDE_CODE_OAUTH_TOKEN` en CI).
        Le mode isolé (#108, `MAESTRO_ISOLATION`) est validé ici aussi — une
        config d'isolation bancale casse au câblage, pas en cours d'exécution.
        Les **bornes de l'arbitrage** (#583) le sont pour la même raison, et elle
        pèse plus lourd : découvrir en plein run qu'une borne est illisible, c'est
        le découvrir sur l'appel d'outil qu'on voulait justement faire trancher.
        """
        mode = _resolve_auth_mode(settings)
        if mode is AuthMode.API_KEY and not settings.anthropic_api_key:
            raise ConfigError(
                "Mode d'authentification 'api_key' sélectionné mais ANTHROPIC_API_KEY "
                "est absente. Renseignez la clé, ou passez CLAUDE_AUTH_MODE=subscription "
                "pour utiliser l'abonnement Claude Code."
            )
        return cls(
            Credentials(
                auth_mode=mode,
                api_key=settings.anthropic_api_key,
                oauth_token=settings.claude_oauth_token,
            ),
            isolation=IsolationConfig.from_settings(settings),
            arbitrage=BornesArbitrage.from_settings(settings),
        )

    def supports(self, model: str) -> bool:
        return model.startswith(self._MODEL_PREFIX)

    def _auth_env(self) -> dict[str, str]:
        """Traduit les `Credentials` en variables d'environnement pour le CLI.

        Neutralise (chaîne vide == non défini) les credentials concurrents de rang
        supérieur pour garantir le mode choisi malgré un environnement ambiant.
        """
        creds = self._credentials
        if creds.auth_mode is AuthMode.API_KEY:
            # On vise le rang 3 : neutraliser le bearer (rang 2), seul concurrent
            # supérieur plausiblement présent dans l'environnement.
            return {
                "ANTHROPIC_AUTH_TOKEN": "",
                "ANTHROPIC_API_KEY": creds.api_key or "",
            }
        # SUBSCRIPTION : neutraliser bearer (rang 2) et clé API (rang 3) pour laisser
        # le CLI retomber sur l'abonnement Claude Code (rang 6) — ou sur un token
        # OAuth explicite (rang 5, CI) s'il est fourni.
        env = {"ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_API_KEY": ""}
        if creds.oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = creds.oauth_token
        return env

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> str:
        """Appel modèle **texte seul** : aucun outil n'est exposé au CLI sous-jacent.

        `tools=[]` (→ `--tools ""`) retire au CLI jusqu'à ses outils par défaut :
        `generate` ne peut ni lire ni écrire de fichier, ni lancer de shell — c'est
        le contrat de la capacité (l'exécution outillée passe par `run_agent`).

        `stderr=` (#346) branche la collecte bornée du stderr du CLI : sans elle,
        un sous-processus qui meurt ne laisse que « Check stderr output for
        details » sur un flux que personne n'écoutait.
        """
        stderr = CollecteurStderr()
        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            env=self._auth_env(),
            tools=[],
            stderr=stderr,
        )
        return await _collect_response(prompt, options, stderr=stderr)

    async def generate_stream(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """`generate`, rendu **par incréments** : le fournisseur de référence streame (#693).

        Mêmes options que `generate` — texte seul, `tools=[]`, collecte du stderr
        — plus `include_partial_messages`, le seul réglage qui sépare les deux :
        il passe `--include-partial-messages` au CLI, qui émet alors les
        événements bruts de l'API Anthropic au fil de la génération. Le texte se
        lit dans les `content_block_delta` de type `text_delta` (`_delta_texte`),
        c'est-à-dire dans le flux **du modèle** et non dans un découpage que nous
        aurions inventé après coup.

        Ce que ce chemin ne perd pas, et c'est la moitié qui ne se voit pas : le
        `ResultMessage` final passe toujours par `report_usage`, donc **tokens,
        coût et durée API sont comptés comme sur l'autre chemin** — un fil
        diffusé coûte ce qu'il coûte et le grand livre le sait (`maestro.telemetry`,
        d'où la trace Langfuse). Streamer une réponse ne doit pas revenir à la
        rendre gratuite.
        """
        stderr = CollecteurStderr()
        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            env=self._auth_env(),
            tools=[],
            stderr=stderr,
            include_partial_messages=True,
        )
        async for morceau in _stream_response(prompt, options, stderr=stderr):
            yield morceau

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
        """Lance une exécution *agentique outillée* de l'Agent SDK dans `workspace`.

        Confie `tools` au modèle et fixe `workspace` comme répertoire de travail
        (`cwd`) : le sous-agent y produit ses fichiers. `permission_mode` est
        `bypassPermissions` — l'exécution est **non interactive** (aucun humain pour
        confirmer), l'isolation reposant sur le répertoire dédié et sur la restriction
        de `tools`.

        `plafond_tours` (#239) alimente le `max_turns` du SDK, qui borne la boucle :
        la valeur vient de l'appelant — le profil de l'agent — et non d'une constante
        de ce fournisseur, qui imposait la même borne à des tours au coût sans commune
        mesure. Elle est reportée telle quelle dans le message de `TurnLimitReached`,
        pour qu'un échec nomme sa borne.

        À `None` — le **défaut** depuis #494 — le SDK ne pose aucune borne : son
        transport ne passe `--max-turns` au CLI que si la valeur est renseignée
        (`_internal/transport/subprocess_cli.py`), donc l'absence de plafond se dit
        en ne disant rien, sans cas particulier à écrire ici. La boucle s'arrête
        alors quand l'agent a fini, quand il échoue, ou quand on l'annule.

        Les serveurs `mcp_serveurs` (#104, déjà résolus) sont montés sur la
        session via `mcp_servers` de l'Agent SDK ; `strict_mcp_config` verrouille
        la session sur **cette seule liste** — aucune configuration MCP ambiante
        (utilisateur, projet, plugin) n'est jamais chargée, serveurs déclarés ou
        pas (permissions scopées, docs/02 §7). La session est alors **pilotée**
        (`ClaudeSDKClient`) : le premier tour n'est envoyé qu'une fois tous les
        serveurs déclarés **connectés** (statut sondé, délai borné) — constat du
        pilote #105 : le CLI enregistre les outils MCP *après* son ouverture de
        session, et un premier tour parti trop tôt s'exécute sans eux, l'agent
        concluant sans ses capacités. Un serveur en échec (démarrage, auth) ou
        jamais connecté à l'échéance lève `McpServerUnavailable` (serveur et
        cause nommés) **avant** tout appel modèle.

        Par défaut, l'isolation est *au niveau du système de fichiers* — un shell
        pourrait en principe adresser des chemins hors du `cwd`. Le renfort est le
        **mode isolé** opt-in (#108, `MAESTRO_ISOLATION=conteneur`) : le CLI — et
        tout ce qu'il lance, outils, Bash, serveurs MCP stdio, code produit —
        tourne alors dans un conteneur Docker durci jetable, seul `workspace`
        étant monté. Le branchement tient en deux options SDK : `cli_path` pointe
        le shim (`maestro-sandbox-shim`) au lieu du CLI, `env` porte le protocole
        `MAESTRO_SANDBOX_*` que le shim traduit en `docker run` (accès accordés
        énumérés dans `maestro.sandbox.container`, doc : docs/17). Le chemin texte
        (`generate`) n'est jamais isolé : il n'expose aucun outil, c'est son contrat.

        `projet` (#226) suit le même chemin : c'est le projet dont `workspace` est
        l'espace dérivé (#224). Le mode isolé le passe au protocole, qui monte cet
        espace — jamais la racine — et masque dans le conteneur ce que le
        périmètre exclut. Hors mode isolé il n'a rien à faire : le `cwd` est déjà
        le bon, et c'est le seul chemin que l'agent voit.

        `politique` (#110) arme le **refus au vol** : un hook PreToolUse — le
        seul point de contrôle consulté sous `bypassPermissions` — confronte
        chaque appel d'outil à la politique allow/deny de l'agent. Un appel
        interdit est refusé avec son motif (le modèle le lit et poursuit) et
        signalé via `on_refus` — jamais mué en échec de run. Les outils déjà
        filtrés au montage ne repassent par ce hook que par sûreté : son vrai
        travail est l'outil MCP refusé individuellement sur un serveur monté.

        `on_arbitrage_acte` (#583) arme le **troisième cran** du même hook : un
        appel classé `ask` (#580) y est suspendu, la demande part sur ce canal
        avec l'outil et ses arguments, et l'issue décide — approuvée, l'appel
        passe ; refusée, `deny` motivé. Sans ce canal, un outil `ask` est
        **refusé** et jamais approuvé par défaut (fail-safe EF-08/ENF-04).

        ⚠ À ne pas confondre avec `on_arbitrage` (#582), plus bas : les deux
        aboutissent au même validateur, mais l'un porte **l'acte qu'on a
        intercepté** et l'autre **la raison qu'un agent a rédigée**. Le second
        est un canal *de plus*, et son silence ne dispense de rien — c'est le
        cadrage du parent #573, et c'est pourquoi ils ont deux noms.

        La borne du hook est **posée explicitement** ici (`HookMatcher(timeout=…)`)
        et pas seulement subie : notre attente d'arbitrage vit sous cette borne
        (`BornesArbitrage`), si bien qu'à l'expiration c'est nous qui rendons un
        `deny` motivé, jamais le CLI par échéance — dont la sémantique d'un hook
        expiré ne doit porter aucun fail-safe. Sans politique il n'y a pas de
        hook du tout, donc rien à borner : c'est le seul cas où la borne du SDK
        s'applique encore, et elle ne garde alors rien.

        `stderr` (#346) : le stderr du CLI — ou du shim, en mode isolé, ce qui y
        fait remonter jusqu'aux erreurs de `docker run` — est collecté ligne à
        ligne, borné, et accroché à l'exception d'un échec. C'est ce qui donne une
        cause à une tentative plantée, là où le SDK ne renvoyait qu'à un flux vide.

        `on_activite` (#479) reçoit, **pendant** l'exécution, ce que l'agent est
        en train de faire. Le SDK donne exactement la matière que le pilote lit
        déjà dans le `.jsonl` du CLI pour son `--verbeux` (#176) — appels
        d'outil, cibles, prose du modèle — et elle était jetée : `_absorbe`
        accumulait sans rien publier. Le régulateur monté ici la publie à débit
        borné, et il est **vidé dans un `finally`** : une tâche courte, ou une
        tâche qui meurt sur une exception, doit dire ses derniers gestes plutôt
        que de les emporter — c'est justement d'une tâche en échec qu'on veut
        savoir ce qu'elle faisait juste avant.

        `on_etapes` (#489) reçoit la **checklist** de l'agent, lue là où il la
        tient déjà : l'entrée de ses appels `TodoWrite`
        (`maestro.providers.checklist`). Elle passe par le même `_absorbe`, pour
        la même raison qu'en #479 — c'est le seul endroit où le flux est observé.
        Elle n'est en revanche **pas régulée** : un agent pose sa liste et la
        recoche, pas plus d'une poignée de fois par tâche, et son appelant ne
        republie que ce qui a changé (`SuiviChecklist.rapporte`). Un régulateur y
        ajouterait une latence sur l'information qu'on veut la plus fraîche, pour
        borner un débit qui ne déborde pas.

        `on_arbitrage` (#582) fait porter au **serveur MCP in-process** `maestro`
        (`maestro.providers.arbitrage`) l'outil `demander_arbitrage(raison)` :
        l'agent qui s'apprête à quelque chose d'irréversible lève la main, l'appel
        attend la décision et la lui rend. C'est le seul des quatre canaux qui
        reparte vers l'agent, et le seul dont une panne se traduit en **refus
        servi** plutôt qu'en silence.

        Ce serveur porte **N outils** depuis #718, et non plus un seul : ce qui
        décide de son contenu est `_outils_maestro`, ce qui décide de son montage
        est `_serveurs_mcp`. Le partage n'est pas cosmétique — c'est lui qui rend
        deux verbes ajoutés séparément (#719, #720) réellement indépendants, au
        lieu de les faire se croiser ici. Un serveur **sans aucun outil** n'est
        pas monté du tout, `demander_arbitrage` seul est monté exactement comme
        avant.

        Trois choix à ne pas défaire dans ce montage. Le serveur n'est **pas
        ajouté à `attendus`** (`_attendus_mcp` ne connaît que les serveurs
        déclarés) : `_attend_serveurs_mcp` existe pour le serveur externe qui met
        du temps à venir — un `npx -y` qui télécharge (#105) —, or un serveur SDK
        est servi **en process** par le SDK lui-même (`type: "sdk"`, déclaré au
        CLI dès l'initialisation) : il n'a rien à connecter, et l'y inscrire
        n'ajouterait qu'un risque de 60 s d'attente sur un canal dont l'absence ne
        doit jamais arrêter une tâche. Le **routage** n'a donc pas bougé non plus
        — session pilotée si et seulement si l'agent déclare des serveurs,
        exactement comme avant ce lot. Et il est monté **après** les serveurs
        déclarés, si bien qu'une déclaration homonyme ne peut pas masquer le canal
        d'un garde-fou (nom réservé, `arbitrage.NOM_SERVEUR`).

        Une politique de permissions (#110) le régit **comme n'importe quel
        outil** : une liste `allow` fermée qui ne le cite pas, ou un `deny`
        dessus, retire à l'agent la possibilité de lever la main. C'est le sens
        sûr — la classification, elle, ne dépend pas de lui — et le refus est
        tracé comme les autres.

        `credit_arbitrage` (#584) est ce que ce fournisseur **rend** à l'appelant
        des deux canaux ci-dessus : une fenêtre ouverte autour de chaque attente,
        et rien de plus. Elle est ouverte aux deux endroits où un appel de l'agent
        est réellement suspendu — le hook pour l'acte intercepté, l'outil MCP pour
        l'agent qui lève la main — parce que ce sont les deux seuls instants dont
        la durée soit celle du blocage. En particulier, elle se **referme quand le
        hook cesse d'attendre**, pas quand la demande aboutit : la demande reste
        en vol, l'agent, lui, a repris son travail.
        """
        env = self._auth_env()
        cli_path: Path | None = None
        if self._isolation is not None:
            cli_path = self._isolation.shim
            env |= self._isolation.env_sandbox(workspace, projet=projet)
        stderr = CollecteurStderr()
        serveurs = _serveurs_mcp(
            mcp_serveurs,
            _outils_maestro(on_arbitrage=on_arbitrage, credit=credit_arbitrage),
        )
        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            env=env,
            cwd=workspace,
            cli_path=cli_path,
            stderr=stderr,
            tools=list(tools),
            allowed_tools=list(tools),
            permission_mode="bypassPermissions",
            max_turns=plafond_tours,
            mcp_servers=serveurs,
            strict_mcp_config=True,
            hooks=(
                {
                    "PreToolUse": [
                        HookMatcher(
                            hooks=[
                                _hook_permissions(
                                    politique,
                                    on_refus,
                                    on_arbitrage_acte,
                                    self._arbitrage,
                                    credit_arbitrage,
                                )
                            ],
                            # Posée, jamais subie (#583) : la borne par défaut du
                            # SDK est de 60 s, et c'est elle qui déciderait de
                            # l'issue d'un arbitrage si on la laissait faire.
                            timeout=self._arbitrage.borne_hook_s,
                        )
                    ]
                }
                if politique is not None
                else None
            ),
        )
        # None quand personne n'écoute : `_absorbe` retombe alors exactement sur
        # son comportement d'avant ce lot, sans même composer de ligne.
        regulateur = None if on_activite is None else RegulateurActivite(on_activite)
        try:
            if not mcp_serveurs:
                return await _collect_response(
                    prompt,
                    options,
                    plafond_tours=plafond_tours,
                    stderr=stderr,
                    regulateur=regulateur,
                    on_etapes=on_etapes,
                )
            return await _collect_response_pilotee(
                prompt,
                options,
                attendus=_attendus_mcp(mcp_serveurs),
                plafond_tours=plafond_tours,
                stderr=stderr,
                regulateur=regulateur,
                on_etapes=on_etapes,
            )
        finally:
            if regulateur is not None:
                regulateur.vider()


def _outil_arbitrage(
    on_arbitrage: Arbitre, credit: CreditArbitrage | None = None
) -> SdkMcpTool[Any]:
    """L'outil `demander_arbitrage(raison)` servi à l'agent (#582).

    Un seul outil, dont l'appel **attend** la décision et rend à l'agent de quoi
    enchaîner. La forme de la réponse ne s'invente pas ici — elle vit dans
    `maestro.providers.arbitrage`, avec le reste du vocabulaire, pour rester
    lisible sans monter de session SDK.

    Trois refus se ressemblent et n'ont pas la même cause ; aucun n'est rendu en
    **erreur d'outil**, ce qui inviterait l'agent à réessayer contre la décision
    qu'on vient de lui rendre :

    - **raison vide** : rien n'a été soumis à personne, donc rien n'est refusé —
      on le lui dit et il rappelle l'outil. Lui servir un refus l'enverrait
      renoncer à une action sur laquelle nul n'a été consulté ;
    - **refus du garde-fou** (dont le fail-safe « pas de validateur ») : la
      décision, motivée, rendue telle quelle ;
    - **canal en erreur** : le callback a levé. On refuse — une panne du canal de
      décision n'a jamais autorisé une action —, et on ne laisse surtout pas
      l'exception remonter : elle tuerait la tâche au moment précis où l'agent
      se montrait prudent.

    L'outil est rendu **séparément de son serveur** pour qu'on puisse l'appeler
    tel quel : ce qui décide ici tient en une poignée de lignes, et les éprouver
    ne doit pas coûter un CLI, un sous-processus et un quota (tests → #579).

    `credit` (#584) mesure l'attente : ici, elle n'est bornée par rien — l'agent
    a levé la main et *attend sa réponse*, il n'y a pas d'échéance de hook à
    respecter — et c'est justement le canal où un délai par tâche faisait le plus
    de dégâts. Un `timeout_s` de dix minutes tuait une tâche dont l'agent s'était
    montré prudent, ce qui apprend exactement le contraire de ce qu'on veut lui
    apprendre. La fenêtre couvre le seul `await` qui bloque, et pas la
    composition de la réponse.
    """

    @tool(NOM_OUTIL, DESCRIPTION_OUTIL, SCHEMA_ENTREE)
    async def demander_arbitrage(args: dict[str, Any]) -> dict[str, Any]:
        raison = str(args.get("raison") or "").strip()
        if not raison:
            texte = RAISON_MANQUANTE
        else:
            try:
                with _fenetre_arbitrage(credit):
                    approuve, detail = await on_arbitrage(raison)
            except Exception as exc:  # noqa: BLE001 — refus servi, jamais une tâche tuée
                approuve, detail = False, CANAL_EN_ERREUR.format(cause=exc)
            texte = reponse(approuve, detail)
        return {"content": [{"type": "text", "text": texte}]}

    return demander_arbitrage


@contextmanager
def _fenetre_arbitrage(credit: CreditArbitrage | None) -> Iterator[None]:
    """Ouvre la fenêtre d'attente du crédit quand il y en a un (#584), sinon ne fait rien.

    Le canal est optionnel — un appelant qui n'a pas de délai à défendre n'a rien
    à mesurer —, et ce petit adaptateur évite d'écrire deux fois le même `await`
    sous un `if`. Deux corps à tenir d'accord pour une différence de mesure
    seraient le premier moyen qu'une des deux branches cesse d'être arbitrée.
    """
    if credit is None:
        yield
        return
    with credit.attente():
        yield


def _outils_maestro(
    *,
    on_arbitrage: Arbitre | None = None,
    credit: CreditArbitrage | None = None,
) -> list[SdkMcpTool[Any]]:
    """Les outils que le serveur `maestro` a **effectivement** à porter (#718).

    Le point d'extension du serveur, et le seul : un verbe nouveau (#719, #720)
    s'y ajoute en un `if` et une ligne, sans toucher ni à `run_agent`, ni au
    porte-outils, ni au montage. C'est tout l'objet de ce lot — deux verbes
    écrits en parallèle se croiseraient ici, sur deux lignes voisines, plutôt
    qu'au milieu du corps de `run_agent`.

    Chaque outil est **conditionné à son canal** : sans callback, personne n'est
    au bout du fil, et servir à l'agent un verbe qui n'aboutit nulle part est
    pire que ne pas le lui servir. La liste rendue peut donc être vide — c'est
    un état normal, que `_serveurs_mcp` traite comme tel.
    """
    outils: list[SdkMcpTool[Any]] = []
    if on_arbitrage is not None:
        outils.append(_outil_arbitrage(on_arbitrage, credit))
    return outils


def _serveur_maestro(outils: Sequence[SdkMcpTool[Any]]) -> McpServerConfig:
    """Le serveur MCP **in-process** `maestro` et les N outils qu'il porte (#582, #718).

    In-process (`type: "sdk"`) et non stdio : il n'y a ni processus à lancer ni
    connexion à attendre — le SDK le sert lui-même, et les callbacks que ses
    outils ferment vivent dans la boucle du moteur, là où sont le garde-fou et le
    journal. C'est aussi pourquoi il ne rejoint jamais les `attendus` de
    `_attend_serveurs_mcp` (cf. `run_agent` et `_attendus_mcp`).

    Il ne décide de rien : ni de ce qu'il porte (`_outils_maestro`), ni du fait
    qu'on le monte (`_serveurs_mcp`). Un outil de plus ne se voit donc pas ici,
    et un serveur vide n'y arrive jamais.
    """
    return create_sdk_mcp_server(name=NOM_SERVEUR, tools=list(outils))


def _serveurs_mcp(
    mcp_serveurs: Sequence[ServeurMcp],
    outils_maestro: Sequence[SdkMcpTool[Any]],
) -> dict[str, McpServerConfig]:
    """Les serveurs MCP de la session : ceux que l'agent déclare, puis le nôtre (#582, #718).

    Deux invariants du montage vivent ici, et nulle part ailleurs :

    - **en dernier**, à dessein : le nom est réservé (`arbitrage.NOM_SERVEUR`),
      et une déclaration homonyme (`core/mcp/<agent>.json`) ne masque pas le
      canal d'un garde-fou. C'est le sens sûr des deux ;
    - **rien à monter, rien de monté** : un serveur sans outil serait un canal
      qui promet sans servir — l'agent y lirait une surface d'écriture vide, et
      la politique de permissions gouvernerait un nom qui ne répond à personne.
      L'absence de canal, elle, n'a jamais à arrêter une tâche (règle de #582) :
      on ne monte pas, et rien d'autre ne change.

    Le troisième invariant — le serveur reste **hors des `attendus`** — se lit
    dans `_attendus_mcp`, qui ne connaît que les serveurs déclarés.
    """
    serveurs: dict[str, McpServerConfig] = {s.nom: _config_mcp_sdk(s) for s in mcp_serveurs}
    if outils_maestro:
        serveurs[NOM_SERVEUR] = _serveur_maestro(outils_maestro)
    return serveurs


def _attendus_mcp(mcp_serveurs: Sequence[ServeurMcp]) -> frozenset[str]:
    """Les serveurs dont on attend la connexion — les **déclarés**, et eux seuls (#582).

    Le serveur `maestro` en est absent par construction : il n'est pas dans
    `mcp_serveurs`, il n'a rien à connecter, et l'attendre ferait risquer 60 s de
    silence (`_attend_serveurs_mcp`) sur un canal dont l'absence ne doit jamais
    arrêter une tâche. Séparé de `_serveurs_mcp` pour que l'invariant se vérifie
    sans monter de session — pas parce que le calcul serait compliqué.
    """
    return frozenset(s.nom for s in mcp_serveurs)


def _hook_permissions(
    politique: PolitiqueOutils,
    on_refus: Callable[[str, str], None] | None,
    on_arbitrage_acte: ArbitreActe | None = None,
    bornes: BornesArbitrage | None = None,
    credit: CreditArbitrage | None = None,
) -> Callable[[HookInput, str | None, HookContext], Any]:
    """Le hook PreToolUse : applique la politique de l'agent, et **arme l'arbitrage** (#110, #583).

    Consulté par le CLI avant **chaque** appel d'outil, y compris sous
    `bypassPermissions` (seul point de contrôle restant dans ce mode). Il rend
    désormais les trois crans de la politique (#580) et non plus deux :

    - `PASSE` → sortie vide, le flux normal continue ;
    - `REFUS` → `permissionDecision: deny` motivé, le modèle lit le motif et
      poursuit sa tâche — comportement de #110, inchangé ;
    - `ARBITRAGE` → l'appel est **suspendu** : la demande part sur
      `on_arbitrage_acte` avec l'outil et ses arguments (`maestro.acte`, #581),
      et l'issue décide. Approuvée, l'appel passe ; refusée, `deny` motivé.

    Depuis #586, l'arbitrage a un **décideur** (`DecisionOutil.decideur`), et le
    hook en applique un lui-même : `auto` — celui qui ne désigne personne — est
    tracé puis laissé passer, sans canal ni attente. Les deux autres
    (`orchestrateur`, `humain`) partent sur `on_arbitrage_acte` : le hook ne
    sait pas *qui* est au bout, et n'a pas à le savoir — c'est le garde-fou qui
    route (`maestro.engine.guardrails`), sur le cran que la demande porte.

    ⚠ `on_arbitrage_acte` n'est pas `on_arbitrage` (#582) : celui-ci intercepte
    un acte, celui-là relaie une demande que l'agent a formulée. Ils aboutissent
    au même validateur et ne transportent pas la même chose — voir
    `maestro.providers.arbitrage`.

    **La borne du hook ne décide jamais à notre place**, et c'est tout l'objet du
    ticket. `HookMatcher.timeout` borne la durée d'un hook (60 s par défaut) et
    la sémantique d'un hook expiré appartient au CLI : elle ne doit pas porter le
    fail-safe. L'attente est donc bornée **sous** cette borne
    (`BornesArbitrage.attente_effective`), et à son expiration c'est **nous** qui
    répondons — `deny` motivé « arbitrage en cours ».

    ⚠ L'attente expirée n'**annule pas** la demande : le `shield` la laisse en
    vol, ce que dit le motif servi à l'agent. C'est la différence entre « refusé »
    et « pas encore tranché ». Son issue est absorbée
    (`_absorbe_arbitrage_tardif`), comme celle d'une réalisation détachée par le
    moteur (#64) : sans cela, une décision qui arrive après coup serait signalée
    par asyncio comme une exception jamais relevée.

    Elle n'est plus **perdue** pour autant (#584) : l'appelant la range dans la
    mémoire de la tâche (`maestro.deliberation.MemoireArbitrage`) et le rappel du
    même acte par l'agent la retrouve sans rouvrir de demande. Le partage vit
    là-bas et pas ici, à dessein — ce hook ne connaît ni la tâche, ni le run, ni
    qui tranche, et deux appels au même outil n'ont d'identité commune que du
    côté qui compose la demande.

    `credit` (#584) est la **fenêtre d'attente** rendue à l'appelant : ouverte
    juste avant de se suspendre, refermée dès que ce hook cesse d'attendre —
    verdict rendu ou borne atteinte —, jamais quand la demande aboutit. C'est
    exactement la durée pendant laquelle l'appel de l'agent est resté en
    suspens, et c'est elle que l'appelant déduit du délai de la tâche.

    Les **trois issues** (approuvée, refusée, toujours en attente) passent par
    `on_refus`, le canal de traçage de l'exécuteur (journal + fil temps réel) —
    comme un refus de politique aujourd'hui. Un acte sensible parti sur accord
    humain doit laisser la même trace qu'un acte écarté : c'est le seul endroit
    où le run dira plus tard *qui* a laissé passer *quoi*.

    Deux **fail-safe**, dans l'esprit d'EF-08/ENF-04 : un outil classé `ask` sans
    canal d'arbitrage câblé est refusé (jamais approuvé par défaut), et un canal
    qui lève l'est aussi (bus en panne — même règle que
    `Guardrails.demande_validation` depuis #9).

    Le hook ne lève jamais : un traçage en échec est avalé — l'observation ne
    casse pas l'exécution observée.
    """
    # Import différé : `maestro.providers` ne dépend pas de `maestro.agents` à
    # l'exécution (cf. le bloc TYPE_CHECKING plus haut). Payé une fois, à la
    # construction du hook, jamais à chaque appel d'outil.
    from maestro.agents.permissions import Verdict

    bornes = bornes or BornesArbitrage()

    def trace(outil: str, motif: str) -> None:
        """Signale l'issue à l'exécuteur — un traçage en panne ne change rien au verdict."""
        if on_refus is None:
            return
        try:
            on_refus(outil, motif)
        except Exception:  # noqa: BLE001 — le traçage ne casse jamais l'exécution
            pass

    def refuse(outil: str, motif: str) -> HookJSONOutput:
        """Compose le `deny` du hook après l'avoir tracé — le seul chemin de refus."""
        trace(outil, motif)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": motif,
            }
        }

    async def arbitre(outil: str, motif: str, entree: object) -> HookJSONOutput:
        """Suspend l'appel le temps de l'arbitrage, et rend son issue — toujours à l'heure."""
        if on_arbitrage_acte is None:
            return refuse(outil, motif_sans_arbitre(outil))
        attente = asyncio.ensure_future(
            on_arbitrage_acte(outil, arguments_depuis(entree), motif)
        )
        try:
            # La fenêtre se referme ici, avec le `with` — pas avec `attente`, qui
            # peut survivre à ce hook. C'est la durée du **blocage de l'appel**,
            # la seule que l'appelant puisse honnêtement rendre à la tâche.
            with _fenetre_arbitrage(credit):
                approuve, detail = await asyncio.wait_for(
                    asyncio.shield(attente), bornes.attente_effective
                )
        except TimeoutError:
            # Deux causes que le type ne distingue pas : notre attente qui expire
            # (la demande est encore en vol, donc `attente` n'est pas soldée) et
            # la demande elle-même qui lève un `TimeoutError` — un bus qui coupe.
            # Elles ne se réparent pas au même endroit, et rendre le motif de
            # l'une pour l'autre enverrait chercher une décision humaine là où
            # c'est un transport qui est tombé.
            if attente.done() and not attente.cancelled() and attente.exception():
                return refuse(outil, motif_panne(outil, attente.exception()))
            attente.add_done_callback(_absorbe_arbitrage_tardif)
            return refuse(outil, motif_attente(outil, bornes.attente_effective))
        except Exception as exc:  # noqa: BLE001 — fail-safe : un canal en panne ne passe pas
            return refuse(outil, motif_panne(outil, exc))
        if not approuve:
            return refuse(outil, motif_refus(outil, detail))
        # Approuvé : on trace, et on rend la sortie vide plutôt qu'un `allow`
        # explicite — l'appel n'a pas besoin d'être *forcé*, il a besoin de ne
        # plus être suspendu, et sous `bypassPermissions` il n'y a rien à lever.
        trace(outil, motif_approbation(outil, detail))
        return {}

    async def hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        outil = str(input_data.get("tool_name") or "")
        if not outil:
            return {}
        decision = politique.decide(outil)
        if decision.verdict is Verdict.PASSE:
            return {}
        if decision.verdict is Verdict.REFUS:
            return refuse(outil, decision.motif)
        if decision.decideur is Decideur.AUTO:
            # Le cran qui ne désigne personne (#586) : rien à soumettre, donc
            # rien à attendre — et surtout aucun canal requis. Le faire passer
            # par `arbitre` le ferait refuser (`motif_sans_arbitre`) chez tout
            # appelant qui exécute hors de la Control Tower, c'est-à-dire
            # refuser un acte dont la politique dit qu'il n'a personne à
            # déranger. Il laisse quand même sa trace : c'est la seule chose qui
            # le distingue d'un `allow`.
            trace(outil, motif_auto(outil))
            return {}
        return await arbitre(outil, decision.motif, input_data.get("tool_input"))

    return hook


def _absorbe_arbitrage_tardif(attente: asyncio.Future[tuple[bool, str]]) -> None:
    """Absorbe l'issue d'un arbitrage qui a dépassé notre attente (#583).

    Pendant exact de `_absorbe_issue_tardive` côté moteur (#64), et pour la même
    raison : la demande est restée en vol après que le hook a répondu, donc son
    issue — décision tardive ou exception du bus — arrive quand plus personne ne
    l'attend. Sans cette relève, asyncio signalerait une « exception never
    retrieved » sur un objet dont on a déjà rendu le verdict.

    Ce qu'elle ne fait **toujours pas**, et c'est voulu : elle ne rattrape rien.
    Le rattrapage d'une décision tardive vit chez l'appelant (#584,
    `maestro.deliberation.MemoireArbitrage`), qui seul sait de quel acte il
    s'agit et à quelle tâche il appartient. Ici on absorbe, et c'est tout — un
    fournisseur qui retiendrait des décisions tiendrait un état de garde-fou
    dans une couche de transport.
    """
    if not attente.cancelled():
        attente.exception()


def _erreur_plafond(plafond_tours: int | None, detail: object) -> TurnLimitReached:
    """Compose l'erreur typée du plafond de tours en **nommant la borne appliquée** (#239).

    Le plafond étant réglé par agent, un « plafond atteint » qui ne dit pas
    *lequel* n'apprend rien : le message porte donc le nombre de tours
    effectivement passé au SDK. Sans borne posée — le chemin texte (`generate`),
    qui n'en fixe jamais, et depuis #494 le **cas courant** des exécutions
    outillées — il n'y a aucun chiffre à citer : le message nomme alors le signal
    reçu, `max_turns`, plutôt que d'inventer une valeur que nous n'avons pas posée.
    C'est un chemin qu'on ne s'attend plus guère à emprunter (rien ne borne la
    boucle quand `--max-turns` n'est pas passé), et c'est bien pourquoi il doit
    rester lisible : s'il se produit, la borne vient d'ailleurs que d'ici.
    """
    borne = f"{plafond_tours} tours" if plafond_tours is not None else "max_turns"
    return TurnLimitReached(f"plafond de tours atteint ({borne}) : {detail}")


def _avec_stderr(exc: _E, stderr: CollecteurStderr | None) -> _E:
    """Accroche à `exc` ce que le CLI a écrit sur stderr — ou la mention qu'il s'est tu (#346).

    Une seule règle, sans cas particulier : **toute** exception qui sort du flux
    SDK repart avec le résumé du collecteur. Ni le type ni le message ne changent
    (c'est un attribut), donc la classification transitoire / non transitoire et
    les erreurs typées de la frontière restent exactement ce qu'elles étaient — le
    stderr ne fait que voyager jusqu'au journal, où l'exécuteur le consigne.
    """
    if stderr is None:
        return exc
    return attache_stderr(exc, stderr.resume())


async def _collect_response(
    prompt: str,
    options: ClaudeAgentOptions,
    *,
    plafond_tours: int | None = None,
    stderr: CollecteurStderr | None = None,
    regulateur: RegulateurActivite | None = None,
    on_etapes: Callable[[Sequence[EtapeTache]], None] | None = None,
) -> str:
    """Déroule `query`, assemble le texte de la réponse et signale l'usage (ticket #8).

    Les noms d'outils sont relevés au fil des blocs `ToolUseBlock` ; le message
    final `ResultMessage` porte tokens, coût et durée API — le tout est remonté
    via `maestro.telemetry.report_usage` (sans effet hors `collect_usage()`).

    Le **plafond de tours** (`max_turns`) est mué en `TurnLimitReached` (#91) :
    c'est le contrat de la couche d'abstraction — le moteur reconnaît ainsi un
    garde-fou déterministe (jamais relancé) sans lire d'erreur propre au SDK.
    `plafond_tours` est la borne posée par l'appelant — `None` quand il n'en pose
    pas, ce qui est le défaut depuis #494 comme ce l'a toujours été sur le chemin
    texte : elle nomme la limite dans le message (#239).

    `stderr` (#346) est le collecteur branché sur les options : tout échec repart
    avec ce que le CLI a écrit, faute de quoi l'exception du SDK renvoie à un flux
    que personne n'a lu.

    `regulateur` (#479) publie l'activité au fil du flux — None quand personne
    n'écoute. `on_etapes` (#489) reçoit la checklist de l'agent, même régime.
    """
    parts: list[str] = []
    outils: list[str] = []
    try:
        async for message in query(prompt=prompt, options=options):
            _absorbe(message, parts, outils, regulateur, on_etapes)
    except Exception as exc:
        if _MARQUEUR_MAX_TURNS in str(exc):
            raise _avec_stderr(_erreur_plafond(plafond_tours, exc), stderr) from exc
        _avec_stderr(exc, stderr)
        raise
    return "".join(parts)


async def _stream_response(
    prompt: str,
    options: ClaudeAgentOptions,
    *,
    stderr: CollecteurStderr | None = None,
) -> AsyncIterator[str]:
    """Déroule `query` en **incréments de texte**, usage signalé comme ailleurs (#693).

    Le pendant streamé de `_collect_response`, et non une variante de celle-ci :
    ce qu'elles absorbent n'est pas la même matière. Avec
    `include_partial_messages`, le flux porte **les deux** — les `StreamEvent`
    au fil de l'écriture, puis l'`AssistantMessage` complet qui les récapitule —
    et prendre les deux compterait chaque phrase deux fois. Le texte vient donc
    des seuls événements de flux, l'`AssistantMessage` ne servant plus qu'à
    relever les outils pour `StepUsage.outils`.

    ⚠ Le repli n'est pas un ornement : si le CLI n'émet **aucun** événement de
    flux (version antérieure au drapeau, sortie sans partiels), ne lire que les
    deltas rendrait une réponse **vide** — l'échec le plus coûteux qui soit, car
    il ne ressemble pas à un échec : le fil dirait « l'agent a rendu une réponse
    vide » sur une réponse que le modèle a bel et bien écrite. On rend alors, en
    un seul morceau, le texte de l'`AssistantMessage` — c'est-à-dire exactement
    ce que la frontière fait par défaut pour un fournisseur qui ne streame pas.
    Le drapeau `diffuse` est ce qui empêche les deux sources de se cumuler.

    L'usage (`ResultMessage`) et le stderr suivent le régime de l'autre chemin,
    au mot près : un flux qui casse repart avec ce que le CLI a écrit (#346), et
    `error_max_turns` reste mué en `TurnLimitReached` (#91) même si aucune borne
    n'est posée ici — le chemin texte n'en fixe pas (`_erreur_plafond` nomme
    alors le signal reçu).
    """
    parts: list[str] = []
    outils: list[str] = []
    diffuse = False
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, StreamEvent):
                texte = _delta_texte(message.event)
                if texte:
                    diffuse = True
                    yield texte
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
                    elif isinstance(block, ToolUseBlock) and block.name not in outils:
                        outils.append(block.name)
            elif isinstance(message, ResultMessage):
                report_usage(_usage_from_result(message, tuple(outils)))
    except Exception as exc:
        if _MARQUEUR_MAX_TURNS in str(exc):
            raise _avec_stderr(_erreur_plafond(None, exc), stderr) from exc
        _avec_stderr(exc, stderr)
        raise
    if not diffuse and parts:
        yield "".join(parts)


def _delta_texte(evenement: object) -> str:
    """Le texte porté par un événement de flux de l'API — `""` pour tout le reste (#693).

    Un `StreamEvent` transporte l'événement **brut** de l'API Anthropic : seuls
    les `content_block_delta` de type `text_delta` portent la réponse en train de
    s'écrire. Tout le reste — `message_start`, `content_block_start`, `ping`,
    deltas d'entrée d'outil, et ce que l'API ajoutera demain — ne dit rien du
    texte et ne rend donc rien.

    La lecture est **tolérante par construction** plutôt que gardée par un
    schéma, et c'est la même règle que `maestro.providers.checklist` : un
    événement d'une forme qu'on ne connaît pas encore vaut « rien à publier »,
    jamais une exception au milieu d'une réponse. Une frontière qui casserait sur
    un champ inattendu ferait d'une nouveauté d'API une panne de chat.
    """
    if not isinstance(evenement, dict) or evenement.get("type") != "content_block_delta":
        return ""
    delta = evenement.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return ""
    texte = delta.get("text")
    return texte if isinstance(texte, str) else ""


async def _collect_response_pilotee(
    prompt: str,
    options: ClaudeAgentOptions,
    *,
    attendus: frozenset[str],
    plafond_tours: int | None = None,
    stderr: CollecteurStderr | None = None,
    regulateur: RegulateurActivite | None = None,
    on_etapes: Callable[[Sequence[EtapeTache]], None] | None = None,
) -> str:
    """Comme `_collect_response`, mais en session pilotée : serveurs MCP connectés d'abord.

    Le CLI ouvre sa session sans attendre ses serveurs MCP (statut « pending »),
    et n'enregistre leurs outils qu'à la connexion : en one-shot, le premier
    tour du modèle partirait sans eux (constat du pilote #105). La session
    pilotée (`ClaudeSDKClient`) n'envoie donc `prompt` qu'une fois tous les
    serveurs `attendus` connectés (`_attend_serveurs_mcp` — échec propre sinon).

    En mode piloté, le CLI signale un résultat en échec (`error_max_turns`,
    `error_during_execution`…) par un `ResultMessage` d'erreur au lieu d'une
    exception : il est mué ici en `TurnLimitReached`/`RuntimeError` pour rendre
    les deux chemins indistinguables vus du moteur.

    `stderr` (#346) suit la même règle que sur l'autre chemin : l'échec repart
    avec ce que le CLI a écrit. Le `try` enveloppe **tout** le bloc, l'ouverture
    de session comprise — un CLI qui meurt au démarrage est précisément le cas
    où l'exception du SDK n'apprend rien.
    """
    parts: list[str] = []
    outils: list[str] = []
    try:
        async with ClaudeSDKClient(options) as client:
            await _attend_serveurs_mcp(client, attendus)
            await client.query(prompt)
            async for message in client.receive_response():
                _absorbe(message, parts, outils, regulateur, on_etapes)
                if isinstance(message, ResultMessage) and message.is_error:
                    detail = message.result or message.subtype
                    if _MARQUEUR_MAX_TURNS in message.subtype:
                        raise _erreur_plafond(plafond_tours, detail)
                    raise RuntimeError(f"Claude Code returned an error result: {detail}")
    except Exception as exc:
        _avec_stderr(exc, stderr)
        raise
    return "".join(parts)


async def _attend_serveurs_mcp(client: ClaudeSDKClient, attendus: frozenset[str]) -> None:
    """Bloque jusqu'à la connexion des serveurs `attendus`, sinon `McpServerUnavailable`.

    Sonde le statut réel des serveurs de la session (`get_mcp_status`) :
    tous connectés → la session peut commencer ; un serveur en échec
    (`_MCP_STATUTS_ECHEC` : démarrage/connexion en échec, authentification
    requise, désactivé) → erreur propre immédiate, serveur et cause nommés ;
    encore en attente à l'échéance (`_MCP_CONNEXION_MAX_S`) → idem, un
    « pending » sans fin est un serveur qui ne viendra pas. C'est la garantie
    du contrat #104 : l'agent ne travaille jamais amputé de ses capacités.
    """
    echeance = monotonic() + _MCP_CONNEXION_MAX_S
    while True:
        statuts = await client.get_mcp_status()
        etats: dict[str, McpServerStatus] = {
            str(entree.get("name")): entree
            for entree in statuts.get("mcpServers", ())
            if isinstance(entree, dict)
        }
        echecs: list[str] = []
        en_attente: list[str] = []
        for nom in sorted(attendus):
            etat = etats.get(nom)
            if etat is None:
                en_attente.append(f"{nom} : absent de la session")
            elif etat.get("status") in _MCP_STATUTS_ECHEC:
                cause = etat.get("error") or f"état « {etat.get('status')} »"
                echecs.append(f"{nom} : {cause}")
            elif etat.get("status") != "connected":
                en_attente.append(f"{nom} : connexion en cours")
        if echecs:
            raise McpServerUnavailable(
                "serveur(s) MCP indisponible(s) — " + " ; ".join(echecs) + "."
            )
        if not en_attente:
            return
        if monotonic() >= echeance:
            raise McpServerUnavailable(
                "serveur(s) MCP indisponible(s) — toujours pas connecté(s) après "
                f"{_MCP_CONNEXION_MAX_S:g} s : " + " ; ".join(en_attente) + "."
            )
        await asyncio.sleep(_MCP_SONDAGE_S)


def _absorbe(
    message: Any,
    parts: list[str],
    outils: list[str],
    regulateur: RegulateurActivite | None = None,
    on_etapes: Callable[[Sequence[EtapeTache]], None] | None = None,
) -> None:
    """Absorbe un message du flux SDK : texte et outils relevés, usage signalé (#8).

    Facteur commun des deux chemins (`query` one-shot, session pilotée) : les
    blocs texte s'ajoutent à `parts`, chaque outil vu une fois à `outils`, et le
    `ResultMessage` remonte tokens/coût/durée via `report_usage` — y compris sur
    un résultat en échec (le coût d'un `error_max_turns` compte au grand livre).

    C'est aussi, depuis #479, **le seul endroit où le flux est observé**, donc le
    seul d'où l'activité peut partir : `regulateur` reçoit chaque bloc d'outil et
    chaque bloc de texte, dans l'ordre, au moment où ils passent. C'était le
    constat du ticket — la matière traversait cette fonction et personne ne la
    publiait.

    Et c'est pour la même raison que la **checklist** de l'agent part d'ici
    (#489) : elle est l'entrée d'un appel d'outil comme un autre, et cet appel
    passait déjà là. `on_etapes` la reçoit **en plus** du régulateur, jamais à sa
    place — poser une case à cocher est aussi un geste, et le taire au fil
    d'activité ferait un trou dans la séquence que #479 existe pour reconstituer.

    ⚠ Les deux comptes ne sont **pas** le même et ne doivent pas être fusionnés.
    `outils` reste **dédupliqué** parce qu'il alimente `StepUsage.outils`, dont
    le contrat est l'union ordonnée des outils employés (`StepUsage.fusion` la
    refait à chaque agrégation) : y laisser les doublons gonflerait la liste du
    grand livre à chaque tâche sans rien y apprendre. Le régulateur, lui, voit
    **chaque occurrence** — c'est exactement la séquence que la déduplication
    détruisait, et elle vit désormais là où on en a besoin plutôt que d'être
    reconstituée depuis un ensemble qui l'a perdue.
    """
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
                if regulateur is not None and block.text.strip():
                    regulateur.note(Geste.jalon(block.text))
            elif isinstance(block, ToolUseBlock):
                if regulateur is not None:
                    regulateur.note(Geste.outil_appele(block.name, block.input))
                if on_etapes is not None and est_checklist(block.name):
                    _publie_etapes(on_etapes, block.input)
                if block.name not in outils:
                    outils.append(block.name)
    elif isinstance(message, ResultMessage):
        report_usage(_usage_from_result(message, tuple(outils)))


def _publie_etapes(
    on_etapes: Callable[[Sequence[EtapeTache]], None], entree: object
) -> None:
    """Lit la checklist de l'agent dans l'entrée de l'outil et la signale (#489).

    Ne lève jamais, aux deux étages : ni la lecture (tolérante par construction,
    `maestro.providers.checklist`), ni le callback — même règle que `on_refus` et
    que le régulateur d'activité. Une liste vide n'est pas signalée : un appel
    illisible dirait « l'agent n'a plus rien à faire » là où il ne dit rien du
    tout, et `SuiviChecklist` effacerait une checklist en place.
    """
    try:
        etapes = etapes_depuis_outil(entree)
        if etapes:
            on_etapes(etapes)
    except Exception:  # noqa: BLE001 — observer ne casse jamais l'observé
        pass


def _config_mcp_sdk(serveur: ServeurMcp) -> McpServerConfig:
    """Traduit une déclaration `ServeurMcp` (résolue) au format `mcp_servers` du SDK.

    C'est la couture fournisseur du #104 : la forme agnostique (commande locale
    ou URL + options) devient la config native de l'Agent SDK — stdio
    (`command`/`args`/`env`) ou distant (`url`/`headers`). Les options vides ne
    sont pas émises (défauts du SDK).
    """
    config: dict[str, Any]
    if serveur.type == "stdio":
        config = {"type": "stdio", "command": serveur.commande}
        if serveur.args:
            config["args"] = list(serveur.args)
        if serveur.env:
            config["env"] = dict(serveur.env)
    else:
        config = {"type": serveur.type, "url": serveur.url}
        if serveur.headers:
            config["headers"] = dict(serveur.headers)
    # La forme est garantie par la validation à la lecture (maestro.agents.mcp) :
    # le cast borne juste le dict dynamique aux TypedDict du SDK.
    return cast("McpServerConfig", config)


def _usage_from_result(result: ResultMessage, outils: tuple[str, ...]) -> StepUsage:
    """Traduit le `ResultMessage` du SDK en mesure d'usage d'un appel modèle.

    `tokens_entree` agrège le prompt direct et le cache (création + lecture) :
    c'est la consommation réelle de l'appel, telle que le SDK la décompose.
    `total_cost_usd` peut être None (le coût reste alors « inconnu », pas nul).
    """
    usage: dict[str, Any] = result.usage or {}
    tokens_entree = sum(
        int(usage.get(cle) or 0)
        for cle in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return StepUsage(
        appels=1,
        tokens_entree=tokens_entree,
        tokens_sortie=int(usage.get("output_tokens") or 0),
        cout_usd=result.total_cost_usd,
        duree_api_ms=result.duration_api_ms,
        tours=result.num_turns,
        outils=outils,
    )


# Auto-enregistrement : importer ce module suffit à rendre « claude » résolvable.
register(ClaudeProvider.name, ClaudeProvider)
