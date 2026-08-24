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
from collections.abc import Callable, Sequence
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
    TextBlock,
    ToolUseBlock,
    query,
)
from claude_agent_sdk.types import HookInput

from maestro.config import ConfigError, Settings
from maestro.detail_tache import EtapeTache
from maestro.providers.activite import Geste, RegulateurActivite
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
        self, credentials: Credentials, *, isolation: IsolationConfig | None = None
    ) -> None:
        self._credentials = credentials
        # Mode isolé (#108) : quand il est actif, `run_agent` lance le CLI dans un
        # conteneur durci via le shim (`cli_path`). None : exécution sur l'hôte
        # (comportement historique, défaut).
        self._isolation = isolation

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
        on_activite: Callable[[str], None] | None = None,
        on_etapes: Callable[[Sequence[EtapeTache]], None] | None = None,
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
        """
        env = self._auth_env()
        cli_path: Path | None = None
        if self._isolation is not None:
            cli_path = self._isolation.shim
            env |= self._isolation.env_sandbox(workspace, projet=projet)
        stderr = CollecteurStderr()
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
            mcp_servers={s.nom: _config_mcp_sdk(s) for s in mcp_serveurs},
            strict_mcp_config=True,
            hooks=(
                {"PreToolUse": [HookMatcher(hooks=[_hook_permissions(politique, on_refus)])]}
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
                attendus=frozenset(s.nom for s in mcp_serveurs),
                plafond_tours=plafond_tours,
                stderr=stderr,
                regulateur=regulateur,
                on_etapes=on_etapes,
            )
        finally:
            if regulateur is not None:
                regulateur.vider()


def _hook_permissions(
    politique: PolitiqueOutils, on_refus: Callable[[str, str], None] | None
) -> Callable[[HookInput, str | None, HookContext], Any]:
    """Le hook PreToolUse qui applique la politique allow/deny de l'agent (#110).

    Consulté par le CLI avant **chaque** appel d'outil, y compris sous
    `bypassPermissions` (seul point de contrôle restant dans ce mode). Un
    appel permis rend une sortie vide (le flux normal continue) ; un appel
    interdit rend un `permissionDecision: deny` motivé — le modèle lit le
    motif et poursuit sa tâche — et signale la violation via `on_refus`
    (canal de traçage de l'exécuteur : journal + fil temps réel). Le hook ne
    lève jamais : un traçage en échec est avalé — l'observation ne casse pas
    l'exécution observée.
    """

    async def hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        outil = str(input_data.get("tool_name") or "")
        if not outil or politique.autorise(outil):
            return {}
        raison = politique.raison_refus(outil)
        if on_refus is not None:
            try:
                on_refus(outil, raison)
            except Exception:  # noqa: BLE001 — le traçage ne casse jamais l'exécution
                pass
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": raison,
            }
        }

    return hook


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
