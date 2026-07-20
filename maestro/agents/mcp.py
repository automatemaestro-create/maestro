"""Serveurs MCP déclarés par agent, montés à l'exécution (ticket #104, parent #101).

Le Model Context Protocol branche des capacités externes (Slack, tickets,
Figma, cloud…) sur les exécutions d'un agent sans coder de connecteur ad hoc.
Ce module porte la moitié **configuration** du socle : la déclaration des
serveurs (`ServeurMcp` — commande locale ou URL + options) et leur dépôt
**hors du code** (`McpStore`, un fichier JSON par agent — même pattern que la
capacité #86), **validé à la lecture** : une déclaration invalide est refusée
avec sa cause, jamais montée à moitié.

Le montage vit dans la couche SDK (`ModelProvider.run_agent(mcp_serveurs=...)`,
fournisseur Claude via l'Agent SDK) : l'exécuteur relit ce dépôt **à chaud** à
chaque tâche — comme les playbooks (#78) — et confie la liste au runtime
outillé. Les serveurs n'équipent donc que les **exécutions outillées** ; le
chemin texte (`generate`, agents sans runtime) n'expose aucun outil, MCP
compris. Au POC le dépôt est sur fichiers (`core/mcp/<agent>.json`, déclaré et
versionné avec le dépôt Git) ; en V1 il passera en base sans changer ce contrat.

Les **secrets** (tokens d'API des serveurs) ne sont **jamais en clair** dans
une déclaration versionnée : les valeurs d'`env`/`headers` portent des
références `${VARIABLE}` résolues depuis l'environnement au moment du montage
(`resolus`) — une variable absente rend le serveur indisponible, erreur propre
avant tout appel modèle. Un serveur déclaré **optionnel** (`"optionnel": true`,
#125) fait exception : ses références non résolues l'**omettent du montage** au
lieu de faire échouer la tâche — c'est le canal des capacités qui ne s'activent
que lorsqu'un humain a fourni le secret (ex. token OAuth du serveur officiel
Figma). Cette convention anticipe le chantier sécurité (#102) ;
le #109 la complète : `environ` peut être le **coffre scopé** de l'agent
(`maestro.agents.secrets.SecretStore.environ`) — un agent ne résout alors que
ses propres secrets — et toute valeur résolue est enregistrée au registre de
rédaction (masquée si elle réapparaît en sortie).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings
from maestro.providers.base import McpServerUnavailable
from maestro.telemetry.redact import enregistre_secret

#: Types de serveurs déclarables : une commande locale (stdio) ou un endpoint
#: distant (sse/http). Le type « sdk » (instance en process) est hors périmètre :
#: une déclaration versionnée ne transporte pas d'objet Python.
TYPES_SERVEUR: tuple[str, ...] = ("stdio", "sse", "http")

#: Nom de serveur admissible : slug sûr, sans séparateur ni point — il devient
#: un dossier de préfixe d'outils (`mcp__<nom>__<outil>`) et une clé de fichier.
_NOM_SERVEUR = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Nom d'agent admissible comme fichier de stockage — même verrou que
#: `maestro.agents.store` : slug sûr, jamais un chemin arbitraire.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Une référence `${VARIABLE}` dans une valeur d'`env`/`headers` : résolue depuis
#: l'environnement au montage (`resolus`), affichée telle quelle par `to_dict`.
_REFERENCE_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Masque des valeurs littérales dans la forme publique d'une déclaration : une
#: valeur sans référence `${VAR}` peut être un secret posé en clair — on ne la
#: réémet jamais vers l'API/l'UI.
_VALEUR_MASQUEE = "•••"


@dataclass(frozen=True)
class ServeurMcp:
    """La déclaration d'un serveur MCP d'un agent — la partie versionnable du contrat.

    Deux formes : une **commande locale** (`type` « stdio » : `commande` +
    `args` + `env`) ou un **endpoint distant** (`type` « sse »/« http » :
    `url` + `headers`). Les valeurs d'`env`/`headers` peuvent référencer
    l'environnement (`${VARIABLE}`) — c'est le canal des secrets, jamais en
    clair dans une déclaration versionnée.

    `optionnel` (#125) : un serveur optionnel dont une référence ne se résout
    pas est **omis du montage** (la tâche s'exécute sans lui) au lieu de la
    faire échouer — pour les capacités activées par un secret fourni par
    l'humain (env aujourd'hui, coffre #109 ou Control Tower demain).
    """

    nom: str
    type: str
    commande: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    optionnel: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Réémet la déclaration en dict JSON-sérialisable, **valeurs masquées**.

        La forme publique (API/UI) : les valeurs d'`env`/`headers` sans
        référence `${VAR}` sont masquées — une déclaration ne devrait jamais
        porter de secret en clair, mais on ne réémet pas ce qui y ressemble.
        """
        return {
            "nom": self.nom,
            "type": self.type,
            "commande": self.commande,
            "args": list(self.args),
            "url": self.url,
            "env": {cle: _masque(valeur) for cle, valeur in self.env.items()},
            "headers": {cle: _masque(valeur) for cle, valeur in self.headers.items()},
            "optionnel": self.optionnel,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ServeurMcp:
        """Reconstruit une déclaration depuis sa forme stockée (sans la valider)."""
        return cls(
            nom=str(data.get("nom", "")),
            type=str(data.get("type", "")),
            commande=str(data.get("commande", "")),
            args=tuple(str(a) for a in data.get("args", ())),
            url=str(data.get("url", "")),
            env={str(k): str(v) for k, v in dict(data.get("env", {})).items()},
            headers={str(k): str(v) for k, v in dict(data.get("headers", {})).items()},
            # Pas de coercition : un « optionnel » non booléen est refusé à la
            # validation plutôt qu'interprété (une chaîne "false" serait vraie).
            optionnel=data.get("optionnel", False),
        )


class McpStore:
    """Dépôt des déclarations MCP par agent, sur fichiers (`<racine>/<agent>.json`).

    Un fichier par agent : `{"serveurs": [...]}` — le nom d'agent fait foi côté
    fichier, comme pour les autres dépôts. Le dépôt est **en lecture** au POC
    (les déclarations s'éditent dans le dépôt Git, l'UI les affiche en lecture
    seule à ce lot) ; les lecteurs (moteur, workers, API) relisent à chaque
    usage — l'application est à chaud, comme les playbooks (#78). `lire` valide
    la déclaration et lève `ValueError` avec sa cause exacte si elle est
    invalide : on ne monte jamais une déclaration douteuse.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSON par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> McpStore:
        """Le dépôt configuré : `MAESTRO_MCP_DIR`, sinon `core/mcp/` du dépôt."""
        settings = settings or load_settings()
        if settings.mcp_dir:
            return cls(Path(settings.mcp_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "mcp")

    def lire(self, agent: str) -> tuple[ServeurMcp, ...]:
        """Les serveurs MCP déclarés pour `agent`, validés — vide s'il n'en déclare pas.

        Lève `ValueError` (cause exacte, agent nommé) si le fichier est
        illisible ou la déclaration invalide : c'est la **validation à la
        lecture** — l'appelant (exécuteur, API) la mue en échec propre plutôt
        que de monter une configuration douteuse.
        """
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return ()
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"déclaration MCP illisible pour l'agent {agent!r} ({chemin.name}) : {exc}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("serveurs"), list):
            raise ValueError(
                f"déclaration MCP invalide pour l'agent {agent!r} ({chemin.name}) : "
                'objet {"serveurs": [...]} attendu.'
            )
        serveurs = tuple(
            _valide(ServeurMcp.from_dict(brut), agent=agent) for brut in data["serveurs"]
        )
        noms = [s.nom for s in serveurs]
        doublons = sorted({nom for nom in noms if noms.count(nom) > 1})
        if doublons:
            raise ValueError(
                f"déclaration MCP invalide pour l'agent {agent!r} : serveurs en "
                f"double : {', '.join(doublons)}."
            )
        return serveurs

    def agents(self) -> tuple[str, ...]:
        """Les noms des agents ayant une déclaration stockée, triés (vide si aucun)."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            sorted(
                chemin.stem
                for chemin in self._racine.glob("*.json")
                if _NOM_AGENT.match(chemin.stem)
            )
        )

    def _chemin(self, agent: str) -> Path:
        """Le fichier de déclaration de `agent`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{agent}.json"


def resolus(serveurs: Sequence[ServeurMcp], environ: Mapping[str, str]) -> tuple[ServeurMcp, ...]:
    """Les déclarations `serveurs`, références `${VARIABLE}` résolues depuis `environ`.

    C'est le passage de la forme **versionnable** (références, jamais de secret)
    à la forme **montable** (valeurs effectives, en mémoire seulement) — appelé
    par le runtime juste avant de confier les serveurs au fournisseur. Une
    variable absente rend le serveur **indisponible**
    (`McpServerUnavailable`) : échec propre, déterministe (jamais relancé),
    avant tout appel modèle. Exception (#125) : un serveur **optionnel** dont
    une référence ne se résout pas est **omis** du résultat — la tâche
    s'exécute sans lui, c'est le contrat des capacités activées par un secret
    fourni par l'humain.
    """
    montables: list[ServeurMcp] = []
    for serveur in serveurs:
        try:
            montables.append(
                replace(
                    serveur,
                    env={k: _resout(v, environ, serveur) for k, v in serveur.env.items()},
                    headers={
                        k: _resout(v, environ, serveur) for k, v in serveur.headers.items()
                    },
                )
            )
        except McpServerUnavailable:
            if not serveur.optionnel:
                raise
    return tuple(montables)


def _resout(valeur: str, environ: Mapping[str, str], serveur: ServeurMcp) -> str:
    """`valeur` avec ses références `${VAR}` remplacées, ou `McpServerUnavailable`.

    Chaque valeur résolue est un secret par convention (#109) : elle est
    enregistrée au registre de rédaction avant d'être servie — masquée si elle
    réapparaît dans un journal, une trace ou un livrable.
    """
    references = _REFERENCE_ENV.findall(valeur)
    manquantes = sorted({nom for nom in references if not environ.get(nom)})
    if manquantes:
        raise McpServerUnavailable(
            f"serveur MCP {serveur.nom!r} non montable : référence(s) non "
            f"résolue(s) : {', '.join(manquantes)} — absente(s) de l'environnement "
            "de résolution (le coffre de l'agent si un coffre est provisionné, "
            "sinon l'environnement du process)."
        )
    for nom in references:
        enregistre_secret(environ[nom])
    return _REFERENCE_ENV.sub(lambda m: environ[m.group(1)], valeur)


def _masque(valeur: str) -> str:
    """La forme publique d'une valeur d'`env`/`headers` : référence visible, littéral masqué."""
    return valeur if _REFERENCE_ENV.search(valeur) else _VALEUR_MASQUEE


def _valide(serveur: ServeurMcp, *, agent: str) -> ServeurMcp:
    """La déclaration `serveur` telle quelle si elle est valide, sinon `ValueError`.

    Chaque forme est verrouillée sur son type : une commande locale (stdio) ne
    porte pas d'URL ni d'en-têtes, un endpoint distant (sse/http) ne porte ni
    commande ni environnement — une déclaration ambiguë est refusée plutôt
    qu'interprétée.
    """
    ou = f"(agent {agent!r})"
    if not _NOM_SERVEUR.match(serveur.nom):
        raise ValueError(
            f"nom de serveur MCP invalide {ou} : {serveur.nom!r} (slug [a-z0-9_-] attendu)."
        )
    ici = f"serveur MCP {serveur.nom!r} {ou}"
    if serveur.type not in TYPES_SERVEUR:
        raise ValueError(
            f"type invalide pour le {ici} : {serveur.type!r} "
            f"(attendu : {', '.join(TYPES_SERVEUR)})."
        )
    if not isinstance(serveur.optionnel, bool):
        raise ValueError(
            f"optionnel invalide pour le {ici} : {serveur.optionnel!r} "
            "(booléen attendu — true/false)."
        )
    if serveur.type == "stdio":
        if not serveur.commande.strip():
            raise ValueError(f"commande vide pour le {ici} (type stdio).")
        if serveur.url or serveur.headers:
            raise ValueError(
                f"url/headers interdits pour le {ici} (type stdio — commande locale)."
            )
    else:
        if not serveur.url.startswith(("http://", "https://")):
            raise ValueError(
                f"url invalide pour le {ici} (type {serveur.type}) : "
                f"{serveur.url!r} (http(s):// attendu)."
            )
        if serveur.commande or serveur.args or serveur.env:
            raise ValueError(
                f"commande/args/env interdits pour le {ici} "
                f"(type {serveur.type} — endpoint distant)."
            )
    if any(not cle.strip() for cle in (*serveur.env, *serveur.headers)):
        raise ValueError(f"clé d'env/headers vide pour le {ici}.")
    return serveur
