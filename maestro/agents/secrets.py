"""Coffre local des secrets par agent (ticket #109, parent #102).

Sort les tokens d'intégration (serveurs MCP : Slack, tickets…) de
l'environnement global du process : chaque agent dispose de son **coffre** —
un fichier local `<racine>/<agent>.json`, jamais versionné — et la résolution
des références `${VARIABLE}` de ses déclarations MCP (#104) se fait dans **ce
coffre seulement**. Un agent ne voit que ses propres secrets : le token GitLab
du QA n'est pas résolvable par le DevOps, et réciproquement.

Le coffre est **opt-in par provisionnement** : tant qu'aucun fichier de coffre
`<agent>.json` n'existe sous la racine (`provisionne` faux — un README seul ne
compte pas), la résolution retombe sur l'environnement du process — le
comportement historique du #104, rien ne casse. Dès le premier coffre écrit,
le scoping est **strict pour tous les agents** : un secret absent du coffre de
l'agent rend le serveur indisponible (`McpServerUnavailable`), même si la
variable traîne dans l'environnement — c'est le contrat du ticket (« un agent
ne voit que les siens »), pas un oubli : un état à moitié migré où certains
agents liraient encore tout l'environnement le violerait silencieusement.

Toute valeur servie est enregistrée au registre de rédaction
(`maestro.telemetry.redact.enregistre_secret`) : si elle réapparaît dans une
sortie (journal, trace Langfuse, rapport, ticket GitLab), elle est masquée —
le masquage suit ce qui a réellement été confié aux agents.

Au POC le dépôt est sur fichiers (`core/secrets/`, couvert par `.gitignore` —
seul son README est versionné) ; en V1 il pourra passer sur un vrai gestionnaire
de secrets (Vault, SOPS…) sans changer ce contrat — c'est l'indirection.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from maestro.config import Settings, load_settings
from maestro.telemetry.redact import enregistre_secret

#: Nom d'agent admissible comme fichier de stockage — même verrou que les
#: dépôts voisins (`maestro.agents.mcp`, `store`) : slug sûr, jamais un chemin.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Nom de variable admissible dans un coffre : la même forme que les références
#: `${VARIABLE}` des déclarations MCP qu'il sert à résoudre.
_NOM_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretStore:
    """Coffre des secrets par agent, sur fichiers (`<racine>/<agent>.json`).

    Un fichier par agent : `{"secrets": {"VARIABLE": "valeur"}}` — le nom
    d'agent fait foi côté fichier, comme pour les autres dépôts. Relu à chaque
    usage (application à chaud, comme les déclarations MCP #104) : un secret
    ajouté ou tourné vaut pour la tâche suivante, sans redémarrage. `lire`
    valide le fichier et lève `ValueError` avec sa cause exacte s'il est
    invalide — on ne résout jamais depuis un coffre douteux.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du coffre (un fichier JSON par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> SecretStore:
        """Le coffre configuré : `MAESTRO_SECRETS_DIR`, sinon `core/secrets/` du dépôt."""
        settings = settings or load_settings()
        if settings.secrets_dir:
            return cls(Path(settings.secrets_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "secrets")

    @property
    def provisionne(self) -> bool:
        """Le coffre est-il provisionné (au moins un fichier `<agent>.json`) ?

        C'est la bascule du scoping : provisionné, chaque agent ne résout que
        dans son coffre ; sinon, la résolution garde l'environnement du
        process (comportement historique #104). Le seuil est le **premier
        coffre écrit**, pas l'existence de la racine : le dépôt versionne un
        README sous `core/secrets/`, la racine existe donc partout.
        """
        return any(self._racine.glob("*.json"))

    def lire(self, agent: str) -> dict[str, str]:
        """Les secrets du coffre de `agent` — vide s'il n'a pas de fichier.

        Chaque valeur servie est enregistrée au registre de rédaction : elle
        sera masquée où qu'elle réapparaisse en sortie. Lève `ValueError`
        (cause exacte, agent nommé) si le fichier est illisible ou invalide.
        """
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return {}
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"coffre de secrets illisible pour l'agent {agent!r} ({chemin.name}) : {exc}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("secrets"), dict):
            raise ValueError(
                f"coffre de secrets invalide pour l'agent {agent!r} ({chemin.name}) : "
                'objet {"secrets": {...}} attendu.'
            )
        secrets: dict[str, str] = {}
        for nom, valeur in data["secrets"].items():
            if not isinstance(nom, str) or not _NOM_VARIABLE.match(nom):
                raise ValueError(
                    f"coffre de secrets invalide pour l'agent {agent!r} : nom de "
                    f"variable {nom!r} (forme [A-Za-z_][A-Za-z0-9_]* attendue)."
                )
            if not isinstance(valeur, str):
                raise ValueError(
                    f"coffre de secrets invalide pour l'agent {agent!r} : la valeur "
                    f"de {nom} doit être une chaîne."
                )
            secrets[nom] = valeur
            enregistre_secret(valeur)
        return secrets

    def environ(self, agent: str) -> Mapping[str, str]:
        """L'environnement de résolution des références `${VAR}` de `agent` (#104).

        Le cœur du scoping : coffre **provisionné** → les secrets de `agent`
        seuls (un agent ne voit que les siens — fichier absent = aucun secret,
        les serveurs à référence deviennent indisponibles, échec propre) ;
        coffre **absent** → l'environnement du process, comportement
        historique. Propage le `ValueError` d'un coffre invalide.
        """
        if not self.provisionne:
            return os.environ
        return self.lire(agent)

    def _chemin(self, agent: str) -> Path:
        """Le fichier de coffre de `agent`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{agent}.json"
