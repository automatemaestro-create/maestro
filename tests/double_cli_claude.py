"""Un double du CLI Claude devant le point de contrôle des appels d'outil (#1304).

La sonde de démarrage (`maestro.providers.controle`) éprouve **ce que le CLI fait
d'un refus** : il consulte le hook `PreToolUse` que Maestro a posé, puis exécute
l'outil — ou non. Ce double joue exactement ce geste-là, et rien d'autre, en
remplaçant `query` dans `maestro.providers.claude` :

- la session **de sonde** (celle qui monte le serveur de la sonde) : le double
  appelle l'outil de la sonde, consulte chaque hook comme le ferait le CLI, puis
  exécute l'outil **sauf** si un hook l'a refusé et qu'on lui a dit d'appliquer
  les refus ;
- toute **autre** session est comptée, et déroule la session factice que le test
  lui confie (`session`), ou rien.

Trois écarts se règlent à la construction, un par manière dont un fournisseur
peut trahir un refus : il ne l'applique pas (`applique_les_refus=False`), il ne
passe pas le nom de l'outil au point de contrôle (`nomme_l_outil=False`), ou
l'agent de la sonde n'appelle rien (`appelle=False`).

Pour exécuter l'outil comme le ferait le CLI, le double retient les outils des
serveurs in-process au moment où le fournisseur les construit
(`create_sdk_mcp_server`, qu'il enveloppe sans rien en changer).

Partagé par les suites qui lancent `ClaudeProvider.run_agent` sous une politique
ou une frontière, parce que toutes y rencontrent désormais la sonde : un seul
double du CLI, pour qu'elles ne divergent pas sur ce qu'un CLI fait d'un refus.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, SdkMcpTool

from maestro.providers import claude as claude_mod
from maestro.providers import controle

#: Une session factice : ce que le double déroule pour une session qui n'est pas la sonde.
Session = Callable[..., AsyncIterator[Any]]


class DoubleCli:
    """Le CLI vu du point de contrôle : il consulte le hook, puis exécute — ou non."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        applique_les_refus: bool = True,
        nomme_l_outil: bool = True,
        appelle: bool = True,
        session: Session | None = None,
    ) -> None:
        self.applique_les_refus = applique_les_refus
        self.nomme_l_outil = nomme_l_outil
        self.appelle = appelle
        self._session = session
        #: Les options de chaque session de sonde, dans l'ordre.
        self.sondes: list[ClaudeAgentOptions] = []
        #: Les options de chaque autre session — celles des agents.
        self.sessions: list[ClaudeAgentOptions] = []
        #: Les sorties que les hooks ont rendues à l'appel de la sonde.
        self.decisions: list[object] = []
        self._outils: dict[str, dict[str, SdkMcpTool[Any]]] = {}

        reel = claude_mod.create_sdk_mcp_server

        def serveur(name: str, version: str = "1.0.0", tools: list | None = None) -> Any:
            self._outils[name] = {outil.name: outil for outil in tools or ()}
            return reel(name=name, version=version, tools=tools)

        monkeypatch.setattr(claude_mod, "create_sdk_mcp_server", serveur)
        monkeypatch.setattr(claude_mod, "query", self.query)

    async def query(self, *, prompt: Any, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        """Le `query` du SDK, tel que le fournisseur l'appelle."""
        if controle.NOM_SERVEUR_SONDE in (options.mcp_servers or {}):
            self.sondes.append(options)
            await self._joue_la_sonde(options)
            return
        self.sessions.append(options)
        if self._session is not None:
            async for message in self._session(prompt=prompt, options=options):
                yield message

    async def _joue_la_sonde(self, options: ClaudeAgentOptions) -> None:
        if not self.appelle:
            return
        appel: dict[str, Any] = {"hook_event_name": "PreToolUse", "tool_input": {}}
        if self.nomme_l_outil:
            appel["tool_name"] = controle.nom_complet_sonde()
        refuse = False
        for matcher in (options.hooks or {}).get("PreToolUse", []):
            for hook in matcher.hooks:
                sortie = await hook(appel, "tu-sonde", {"signal": None})
                self.decisions.append(sortie)
                specifique = (sortie or {}).get("hookSpecificOutput", {})
                refuse = refuse or specifique.get("permissionDecision") == "deny"
        if refuse and self.applique_les_refus:
            return
        outil = self._outils[controle.NOM_SERVEUR_SONDE][controle.NOM_OUTIL_SONDE]
        await outil.handler({})
