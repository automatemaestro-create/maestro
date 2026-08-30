"""Serveurs MCP : déclaration, pool projet + activation par agent, montés à l'exécution.

Le Model Context Protocol branche des capacités externes (Slack, tickets,
Figma, cloud…) sur les exécutions d'un agent sans coder de connecteur ad hoc.
Ce module porte la moitié **configuration** du socle : la déclaration des
serveurs (`ServeurMcp` — commande locale ou URL + options) et leur dépôt
**hors du code** (`McpStore`), **validé à la lecture** : une déclaration
invalide est refusée avec sa cause, jamais montée à moitié (ticket #104,
parent #101).

Deux modèles de configuration coexistent, composés par `McpStore.lire(agent)` :

- **hérité** (#104) — *un fichier par agent* `<agent>.json`
  (`{"serveurs": [...]}`), même pattern que la capacité #86 ;
- **pool projet + activation** (#130, parent #129) — une intégration
  (`IntegrationMcp` : un `id` + une déclaration `ServeurMcp`) est déclarée
  **une fois** dans le *pool projet* (`pool.json`, `{"integrations": [...]}`),
  puis **activée par agent** via son `id` (`activations.json`,
  `{"<agent>": ["id", …]}`). Deux agents partagent ainsi la même intégration
  (ex. Figma) sans dupliquer ni la déclaration ni le secret — l'objectif du
  parent #129 (configurer les MCP depuis la Control Tower).

`lire(agent)` retourne la **composition** de ces deux sources : la déclaration
héritée de l'agent, plus les intégrations du pool activées pour lui. Sans pool
ni activation, le résultat est exactement la déclaration héritée — la
**rétro-compatibilité** du #104 : les runs qui s'appuient encore sur
`core/mcp/<agent>.json` ne cassent pas. La migration héritée → pool est
**outillée** (`composer_migration`/`migrer`) mais jamais imposée : les deux
formes cohabitent, l'héritée restant autoritaire à la lecture tant que son
fichier n'est pas retiré.

⚠ Deux migrations cohabitent, et elles ne visent pas la même situation (#263) :

- `migrer()` **compose tout le dépôt d'un coup** et le pool qu'il écrit *est* le
  résultat — c'est un remplacement intégral, donc l'outil d'un projet qui n'a
  encore rien au pool (un `ecrire_pool` de plus effacerait ce que la Control
  Tower y a ajouté depuis) ;
- `migrer_agent(agent)` traite **un agent** et **ajoute** : les intégrations déjà
  au pool y restent, celles de l'agent s'y greffent (mutualisées quand la
  déclaration stockée est identique à une intégration existante), ses activations
  s'y ajoutent sans écraser les autres, et son fichier hérité part. C'est le
  geste que la fiche d'un agent propose, un agent à la fois, sur un pool vivant.


Le montage vit dans la couche SDK (`ModelProvider.run_agent(mcp_serveurs=...)`,
fournisseur Claude via l'Agent SDK) : l'exécuteur relit ce dépôt **à chaud** à
chaque tâche — comme les playbooks (#78) — et confie la liste au runtime
outillé. Les serveurs n'équipent donc que les **exécutions outillées** ; le
chemin texte (`generate`, agents sans runtime) n'expose aucun outil, MCP
compris. Au POC le dépôt est sur fichiers (`core/mcp/`, déclaré et versionné
avec le dépôt Git) ; en V1 il passera en base sans changer ce contrat.

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
import os
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

#: Id d'intégration du pool projet (#130) : le handle stable qu'un agent active.
#: Même slug sûr qu'un nom d'agent ou de serveur — jamais un chemin ni une clé
#: JSON exotique.
_ID_INTEGRATION = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Une référence `${VARIABLE}` dans une valeur d'`env`/`headers` : résolue depuis
#: l'environnement au montage (`resolus`), affichée telle quelle par `to_dict`.
_REFERENCE_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Masque des valeurs littérales dans la forme publique d'une déclaration : une
#: valeur sans référence `${VAR}` peut être un secret posé en clair — on ne la
#: réémet jamais vers l'API/l'UI.
_VALEUR_MASQUEE = "•••"

#: Fichiers du dépôt qui ne sont **pas** « un fichier par agent » : le pool
#: projet des intégrations et la table d'activation par agent (#130). Leurs stems
#: sont exclus du listing des agents et interdits comme nom d'agent — sans quoi
#: `pool`/`activations` passeraient pour des agents et se disputeraient le chemin.
_FICHIER_POOL = "pool.json"
_FICHIER_ACTIVATIONS = "activations.json"
_STEMS_RESERVES = frozenset({"pool", "activations"})


@dataclass(frozen=True)
class ServeurMcp:
    """La déclaration d'un serveur MCP — la partie versionnable du contrat.

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

    def to_stored_dict(self) -> dict[str, Any]:
        """Réémet la déclaration pour le **stockage** (fichier versionné) : valeurs brutes.

        Contrairement à `to_dict` (forme publique API/UI, littéraux masqués),
        la forme stockée conserve les valeurs d'`env`/`headers` telles quelles :
        les références `${VAR}` doivent être réécrites intactes pour rester
        résolvables au montage. Un dépôt bien tenu n'y met que des références,
        jamais de secret en clair (chantier sécurité #102).
        """
        return {
            "nom": self.nom,
            "type": self.type,
            "commande": self.commande,
            "args": list(self.args),
            "url": self.url,
            "env": dict(self.env),
            "headers": dict(self.headers),
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


@dataclass(frozen=True)
class IntegrationMcp:
    """Une entrée du **pool projet** : un id d'intégration + sa déclaration de serveur (#130).

    Le pool projet (parent #129) sort la configuration MCP du modèle « un
    fichier par agent » : une intégration est déclarée **une fois** (secret par
    `${VAR}` compris), puis **activée par agent** via son `id`. Plusieurs agents
    partagent ainsi la même intégration (ex. Figma) sans dupliquer la
    déclaration ni le secret.

    - `id` : le handle stable qu'une activation référence (slug sûr) ;
    - `serveur` : la déclaration `ServeurMcp` (type, commande/url, secrets
      `${VAR}`, `optionnel`) — le `serveur.nom` reste le préfixe d'outils
      (`mcp__<nom>__…`), un concept distinct de l'`id` d'intégration.
    """

    id: str
    serveur: ServeurMcp

    def to_dict(self) -> dict[str, Any]:
        """Forme publique (API/UI) : l'id + la déclaration à secrets masqués."""
        return {"id": self.id, "serveur": self.serveur.to_dict()}

    def to_stored_dict(self) -> dict[str, Any]:
        """Forme stockée (fichier versionné) : l'id + la déclaration à valeurs brutes."""
        return {"id": self.id, "serveur": self.serveur.to_stored_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IntegrationMcp:
        """Reconstruit une intégration depuis sa forme stockée (sans la valider)."""
        return cls(
            id=str(data.get("id", "")),
            serveur=ServeurMcp.from_dict(dict(data.get("serveur", {}))),
        )


@dataclass(frozen=True)
class MigrationAgent:
    """Ce qu'une migration héritée → pool a fait pour **un** agent (#263).

    Le résultat est rendu en trois listes parce que les trois se lisent
    différemment côté écran : `ajoutees` sont les intégrations que la migration a
    **créées** au pool, `reprises` celles qu'elle y a **retrouvées** (même
    déclaration stockée — le partage entre agents que le pool existe pour
    permettre), et `activations` l'ensemble activé de l'agent **après** coup.
    Les fondre en une seule ferait dire « 2 intégrations ajoutées au projet » à
    une migration qui n'en a créé qu'une.
    """

    agent: str
    ajoutees: tuple[IntegrationMcp, ...]
    reprises: tuple[IntegrationMcp, ...]
    activations: tuple[str, ...]
    fichier_retire: bool


class McpStore:
    """Dépôt des configurations MCP, sur fichiers (`<racine>/…`).

    Trois fichiers cohabitent sous la racine :

    - `<agent>.json` (`{"serveurs": [...]}`) — la déclaration **héritée** d'un
      agent (#104), un fichier par agent ;
    - `pool.json` (`{"integrations": [...]}`) — le **pool projet** des
      intégrations partagées (#130) ;
    - `activations.json` (`{"<agent>": ["id", …]}`) — les intégrations du pool
      **activées** par agent (#130).

    `lire(agent)` compose l'héritée et le pool activé (voir sa docstring) ; les
    lecteurs (moteur, workers, API) relisent à chaque usage — l'application est
    à chaud, comme les playbooks (#78). Toute lecture **valide** sa source et
    lève `ValueError` avec sa cause exacte si elle est invalide : on ne compose
    jamais une configuration douteuse. Le pool et les activations sont aussi
    **écrivables** (`ecrire_pool`/`ecrire_activations`) — la Control Tower en
    devient la source (#129), l'écriture reste atomique et versionnée.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (fichiers hérités par agent + pool + activations)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> McpStore:
        """Le dépôt configuré : `MAESTRO_MCP_DIR`, sinon `core/mcp/` du dépôt."""
        settings = settings or load_settings()
        if settings.mcp_dir:
            return cls(Path(settings.mcp_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "mcp")

    def lire(self, agent: str) -> tuple[ServeurMcp, ...]:
        """Les serveurs MCP effectifs de `agent` : la composition pool ∩ activations (#130).

        Compose deux sources, dans l'ordre :

        1. la déclaration **héritée** `<agent>.json` (#104, un fichier par
           agent) — inchangée, pour ne pas casser les runs qui s'y appuient ;
        2. les intégrations du **pool projet** (`pool.json`) **activées** pour
           l'agent (`activations.json`).

        Rétro-compat : sans activation, le résultat est exactement la
        déclaration héritée (comportement du #104 — le pool n'est même pas lu).
        En cas de collision de `serveur.nom` entre l'héritée et une intégration
        activée, l'**héritée l'emporte** : la source qu'un run existant utilise
        déjà reste autoritaire jusqu'à ce que son fichier soit retiré (migration).

        Lève `ValueError` (cause exacte, agent nommé) si une source est
        illisible ou invalide, ou si une activation référence une intégration
        absente du pool — même « validation à la lecture » que le socle : on ne
        monte jamais une configuration douteuse. Vide s'il n'y a ni fichier
        hérité ni activation.
        """
        heritees = self._lire_fichier_agent(agent)
        actives = self.activations(agent)
        if not actives:
            return heritees
        pool = {integ.id: integ for integ in self.pool()}
        inconnues = sorted(i for i in actives if i not in pool)
        if inconnues:
            raise ValueError(
                f"activation MCP invalide pour l'agent {agent!r} : intégration(s) "
                f"absente(s) du pool : {', '.join(inconnues)}."
            )
        composee = list(heritees)
        noms = {s.nom for s in composee}
        for id_ in actives:
            serveur = pool[id_].serveur
            if serveur.nom in noms:
                continue  # l'héritée est autoritaire (rétro-compat, cf. docstring)
            composee.append(serveur)
            noms.add(serveur.nom)
        return tuple(composee)

    def heritees(self, agent: str) -> tuple[ServeurMcp, ...]:
        """Les serveurs de la **seule** déclaration héritée `<agent>.json` (#104) — () si absente.

        La moitié que `lire` compose avec le pool, rendue **seule** (#263). Ce
        n'est pas une commodité : la fiche d'un agent doit dire ce que son
        fichier hérité porte *encore*, et le déduire de la composition — les
        serveurs montés moins ceux des intégrations activées, rapprochés par leur
        **nom** — se trompe dès qu'une intégration du pool a été renommée à
        l'ajout (le nom ne concorde plus, l'héritée réapparaît). La question
        « qu'y a-t-il dans le fichier ? » se pose donc au fichier.

        Lève `ValueError` (cause exacte, agent nommé) si le fichier est illisible
        ou invalide — même validation à la lecture que `lire`.
        """
        return self._lire_fichier_agent(agent)

    def pool(self) -> tuple[IntegrationMcp, ...]:
        """Le pool projet des intégrations MCP (`pool.json`), validé — () si absent (#130).

        Chaque intégration = un `id` (slug unique) + une déclaration
        `ServeurMcp` (secrets par `${VAR}`). Lève `ValueError` (cause exacte) si
        le fichier est illisible, la forme inattendue, un id ou une déclaration
        invalide, ou deux intégrations de même id — on ne compose jamais depuis
        un pool douteux.
        """
        chemin = self._racine / _FICHIER_POOL
        if not chemin.is_file():
            return ()
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"pool MCP illisible ({_FICHIER_POOL}) : {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("integrations"), list):
            raise ValueError(
                f"pool MCP invalide ({_FICHIER_POOL}) : objet "
                '{"integrations": [...]} attendu.'
            )
        integrations = tuple(
            _valide_integration(IntegrationMcp.from_dict(brut)) for brut in data["integrations"]
        )
        doublons = _doublons(i.id for i in integrations)
        if doublons:
            raise ValueError(
                f"pool MCP invalide ({_FICHIER_POOL}) : intégrations en double : "
                f"{', '.join(doublons)}."
            )
        return integrations

    def ecrire_pool(
        self, integrations: Sequence[IntegrationMcp]
    ) -> tuple[IntegrationMcp, ...]:
        """Réécrit le pool projet (remplacement intégral), après validation. Le renvoie (#130).

        Écriture **atomique** (fichier temporaire puis renommage), forme lisible
        (indentée) et valeurs brutes (références `${VAR}` intactes) — c'est de la
        configuration versionnée, comme les déclarations héritées. Lève
        `ValueError` **sans rien écrire** si une intégration est invalide ou si
        deux partagent un id : le dépôt ne stocke jamais un pool inapplicable.
        """
        propres = tuple(_valide_integration(i) for i in integrations)
        doublons = _doublons(i.id for i in propres)
        if doublons:
            raise ValueError(
                f"pool MCP invalide : intégrations en double : {', '.join(doublons)}."
            )
        self._ecrire_json(
            self._racine / _FICHIER_POOL,
            {"integrations": [i.to_stored_dict() for i in propres]},
        )
        return propres

    def activations(self, agent: str) -> tuple[str, ...]:
        """Les ids d'intégrations du pool **activées** pour `agent` (#130) — () si aucune.

        Lit la table d'activation `activations.json` (`{"<agent>": ["id", …]}`).
        Ordre préservé, doublons écartés. Lève `ValueError` si la table est
        illisible, de forme inattendue, ou porte un agent/id hors slug.
        """
        return self._table_activations().get(agent, ())

    def ecrire_activations(self, agent: str, ids: Sequence[str]) -> tuple[str, ...]:
        """Fixe les intégrations activées pour `agent` (remplacement intégral). Les renvoie (#130).

        Fusionne avec la table existante — les **autres agents sont préservés** —
        puis écrit atomiquement. Ids validés (slug), doublons écartés, ordre
        préservé. Une liste **vide retire** l'entrée de l'agent. Lève
        `ValueError` sans rien écrire si le nom d'agent ou un id est hors slug.
        """
        if not _NOM_AGENT.match(agent) or agent in _STEMS_RESERVES:
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        propres: list[str] = []
        for i in ids:
            if not _ID_INTEGRATION.match(i):
                raise ValueError(
                    f"id d'intégration invalide : {i!r} (slug [a-z0-9_-] attendu)."
                )
            if i not in propres:
                propres.append(i)
        table = self._table_activations()
        if propres:
            table[agent] = tuple(propres)
        else:
            table.pop(agent, None)
        self._ecrire_json(
            self._racine / _FICHIER_ACTIVATIONS,
            {a: list(v) for a, v in sorted(table.items())},
        )
        return tuple(propres)

    def composer_migration(
        self,
    ) -> tuple[tuple[IntegrationMcp, ...], dict[str, tuple[str, ...]]]:
        """Calcule le pool + les activations équivalents aux déclarations héritées (#130).

        Parcourt les fichiers `<agent>.json`, **mutualise** les serveurs
        identiques (même déclaration stockée) en une seule intégration
        partagée, et produit l'activation de chaque agent. Ne touche à rien :
        renvoie le couple `(pool, activations)` — à `ecrire_pool`/
        `ecrire_activations` de le persister, à l'appelant (UI #133, script) de
        décider quand retirer les fichiers hérités. Deux agents partageant la
        même intégration (ex. Figma) la déclarent alors **une fois**, activée
        des deux côtés — l'objectif du pool.

        Déterministe : agents et serveurs parcourus dans l'ordre, ids dérivés du
        `serveur.nom` (suffixe `-2`, `-3`… en cas de collision de nom entre
        déclarations distinctes). Propage le `ValueError` d'un fichier hérité
        invalide.
        """
        integrations: list[IntegrationMcp] = []
        id_par_signature: dict[str, str] = {}
        ids_pris: set[str] = set()
        activations: dict[str, tuple[str, ...]] = {}
        for agent in self._agents_fichiers():
            ids_agent: list[str] = []
            for serveur in self._lire_fichier_agent(agent):
                signature = _signature(serveur)
                id_ = id_par_signature.get(signature)
                if id_ is None:
                    id_ = _id_libre(serveur.nom, ids_pris)
                    ids_pris.add(id_)
                    id_par_signature[signature] = id_
                    integrations.append(IntegrationMcp(id=id_, serveur=serveur))
                if id_ not in ids_agent:
                    ids_agent.append(id_)
            if ids_agent:
                activations[agent] = tuple(ids_agent)
        return tuple(integrations), activations

    def migrer(self, *, retirer_fichiers: bool = False) -> tuple[IntegrationMcp, ...]:
        """Persiste le pool + les activations issus des fichiers hérités, en une fois (#130).

        `composer_migration` puis `ecrire_pool`/`ecrire_activations`. Avec
        `retirer_fichiers`, supprime ensuite les `<agent>.json` migrés — le pool
        devient l'unique source ; sans, les deux formes coexistent (l'héritée
        reste autoritaire à la lecture, cf. `lire`). Idempotent tant que les
        fichiers hérités ne changent pas. Renvoie le pool écrit.
        """
        integrations, activations = self.composer_migration()
        self.ecrire_pool(integrations)
        for agent, ids in activations.items():
            self.ecrire_activations(agent, ids)
        if retirer_fichiers:
            for agent in activations:
                self._chemin(agent).unlink(missing_ok=True)
        return integrations

    def migrer_agent(
        self,
        agent: str,
        *,
        ids: Mapping[str, str] | None = None,
        retirer_fichier: bool = True,
    ) -> MigrationAgent:
        """Migre la déclaration héritée d'**un** agent vers le pool, en ajoutant (#263).

        Le geste que la fiche d'un agent propose, et le pendant *additif* de
        `migrer` (voir l'en-tête du module) : chaque serveur de `<agent>.json`
        rejoint le pool — **mutualisé** avec une intégration existante quand leur
        déclaration stockée est identique, créé sinon —, l'agent l'active, et son
        fichier hérité est retiré. Rien d'autre ne bouge : les intégrations du
        pool restent, les activations des autres agents aussi, et celles que cet
        agent avait déjà sont **conservées** (l'ensemble est une union, jamais un
        remplacement).

        Le fichier part par défaut, et c'est le contenu du geste : le laisser
        rendrait la migration invisible — l'héritée reste autoritaire à la
        lecture (`lire`), donc le serveur monté resterait celui du fichier et le
        bloc « hérités » de l'écran ne disparaîtrait pas. `retirer_fichier=False`
        n'existe que pour éprouver la coexistence.

        `ids` propose un id d'intégration par **nom de serveur**, pour un serveur
        que l'appelant sait reconnaître (l'API rapproche la déclaration d'une
        entrée de la bibliothèque, #131 — sans quoi une intégration migrée serait
        montée sans mode d'auth ni fiche, hors de l'allowlist en apparence). Le
        dépôt, lui, ne connaît aucun registre : il prend l'id proposé s'il est
        libre, dérive du `serveur.nom` sinon — jamais l'inverse.

        Lève `ValueError` **sans rien écrire** si l'agent n'a pas de déclaration
        héritée, si son nom est hors slug, ou si une source est invalide.
        """
        heritees = self.heritees(agent)
        if not heritees:
            raise ValueError(
                f"aucune déclaration héritée à migrer pour l'agent {agent!r} "
                f"({agent}.json absent) — son pool est déjà la seule source."
            )
        pool = list(self.pool())
        id_par_signature = {_signature(i.serveur): i.id for i in pool}
        ids_pris = {i.id for i in pool}
        ajoutees: list[IntegrationMcp] = []
        reprises: list[IntegrationMcp] = []
        a_activer: list[str] = []
        for serveur in heritees:
            signature = _signature(serveur)
            id_ = id_par_signature.get(signature)
            if id_ is None:
                propose = (ids or {}).get(serveur.nom) or serveur.nom
                if not _ID_INTEGRATION.match(propose):
                    propose = serveur.nom
                id_ = _id_libre(propose, ids_pris)
                ids_pris.add(id_)
                id_par_signature[signature] = id_
                integration = IntegrationMcp(id=id_, serveur=serveur)
                pool.append(integration)
                ajoutees.append(integration)
            else:
                reprises.append(next(i for i in pool if i.id == id_))
            if id_ not in a_activer:
                a_activer.append(id_)
        # L'écriture du pool passe en premier : elle valide tout ce qu'on ajoute
        # et lève sans rien écrire, donc une déclaration fautive n'a pas laissé
        # d'activation orpheline derrière elle — ce que `lire` refuserait ensuite.
        self.ecrire_pool(pool)
        activations = list(self.activations(agent))
        for id_ in a_activer:
            if id_ not in activations:
                activations.append(id_)
        self.ecrire_activations(agent, activations)
        if retirer_fichier:
            self._chemin(agent).unlink(missing_ok=True)
        return MigrationAgent(
            agent=agent,
            ajoutees=tuple(ajoutees),
            reprises=tuple(reprises),
            activations=tuple(activations),
            fichier_retire=retirer_fichier,
        )

    def agents(self) -> tuple[str, ...]:
        """Les noms des agents ayant une configuration MCP, triés (vide si aucun).

        Union des agents déclarant un fichier hérité `<agent>.json` et des
        agents portant une activation dans le pool (#130). Les fichiers réservés
        du dépôt (`pool.json`, `activations.json`) n'y figurent pas — ce ne sont
        pas des agents.
        """
        noms = set(self._agents_fichiers())
        noms.update(self._table_activations())
        return tuple(sorted(noms))

    def _agents_fichiers(self) -> tuple[str, ...]:
        """Les stems des fichiers hérités `<agent>.json`, triés — hors fichiers réservés."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            sorted(
                chemin.stem
                for chemin in self._racine.glob("*.json")
                if _NOM_AGENT.match(chemin.stem) and chemin.stem not in _STEMS_RESERVES
            )
        )

    def _lire_fichier_agent(self, agent: str) -> tuple[ServeurMcp, ...]:
        """Les serveurs de la déclaration héritée `<agent>.json` (#104), validés — () si absente.

        Lève `ValueError` (cause exacte, agent nommé) si le fichier est illisible
        ou la déclaration invalide : la **validation à la lecture** du socle —
        l'appelant (exécuteur, API) la mue en échec propre plutôt que de monter
        une configuration douteuse.
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
            _valide(ServeurMcp.from_dict(brut), contexte=f"(agent {agent!r})")
            for brut in data["serveurs"]
        )
        doublons = _doublons(s.nom for s in serveurs)
        if doublons:
            raise ValueError(
                f"déclaration MCP invalide pour l'agent {agent!r} : serveurs en "
                f"double : {', '.join(doublons)}."
            )
        return serveurs

    def _table_activations(self) -> dict[str, tuple[str, ...]]:
        """La table d'activation `activations.json`, validée — {} si absente (#130).

        `{"<agent>": ["id", …]}` : nom d'agent et ids en slug, doublons d'ids
        écartés, ordre préservé. Lève `ValueError` (cause exacte) si la table est
        illisible, de forme inattendue, ou porte un agent/id hors slug.
        """
        chemin = self._racine / _FICHIER_ACTIVATIONS
        if not chemin.is_file():
            return {}
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"activations MCP illisibles ({_FICHIER_ACTIVATIONS}) : {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"activations MCP invalides ({_FICHIER_ACTIVATIONS}) : objet "
                '{"<agent>": ["id", …]} attendu.'
            )
        table: dict[str, tuple[str, ...]] = {}
        for agent, ids in data.items():
            if not isinstance(agent, str) or not _NOM_AGENT.match(agent):
                raise ValueError(
                    f"activations MCP invalides ({_FICHIER_ACTIVATIONS}) : nom "
                    f"d'agent {agent!r} (slug [a-z0-9_-] attendu)."
                )
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError(
                    f"activations MCP invalides pour l'agent {agent!r} : liste d'ids "
                    "(chaînes) attendue."
                )
            propres: list[str] = []
            for i in ids:
                if not _ID_INTEGRATION.match(i):
                    raise ValueError(
                        f"activations MCP invalides pour l'agent {agent!r} : id "
                        f"d'intégration {i!r} (slug [a-z0-9_-] attendu)."
                    )
                if i not in propres:
                    propres.append(i)
            table[agent] = tuple(propres)
        return table

    def _ecrire_json(self, chemin: Path, data: Any) -> None:
        """Écrit `data` en JSON à `chemin`, **atomiquement** et lisible (config versionnée)."""
        self._racine.mkdir(parents=True, exist_ok=True)
        temporaire = chemin.with_suffix(chemin.suffix + ".tmp")
        temporaire.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporaire, chemin)

    def _chemin(self, agent: str) -> Path:
        """Le fichier hérité de `agent`, nom validé (jamais un chemin ni un fichier réservé)."""
        if not _NOM_AGENT.match(agent) or agent in _STEMS_RESERVES:
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


def references_env(serveur: ServeurMcp) -> tuple[str, ...]:
    """Les noms de variables `${VAR}` référencés par les valeurs d'`env`/`headers` de `serveur`.

    Le **sous-ensemble** des valeurs qui portent une référence `${VARIABLE}` —
    donc les secrets/identifiants qu'il faut fournir pour monter le serveur (par
    opposition aux littéraux de configuration, ex. `GITLAB_TOOLSETS=issues`).
    L'UI de configuration (lot #133) s'en sert pour rapprocher une intégration du
    pool de l'état de ses secrets dans le coffre projet. Ordre d'apparition
    préservé, doublons écartés.
    """
    noms: list[str] = []
    for valeur in (*serveur.env.values(), *serveur.headers.values()):
        for nom in _REFERENCE_ENV.findall(valeur):
            if nom not in noms:
                noms.append(nom)
    return tuple(noms)


def _masque(valeur: str) -> str:
    """La forme publique d'une valeur d'`env`/`headers` : référence visible, littéral masqué."""
    return valeur if _REFERENCE_ENV.search(valeur) else _VALEUR_MASQUEE


def valide_serveur(serveur: ServeurMcp, *, source: str = "déclaration") -> ServeurMcp:
    """La déclaration `serveur` telle quelle si elle est valide, sinon `ValueError`.

    Le même contrôle que `McpStore.lire` applique à chaque serveur d'un fichier,
    exposé pour les producteurs de déclarations hors dépôt (ex. le registre curé
    #131, qui instancie un template en `ServeurMcp` et refuse une entrée mal
    formée avec sa cause). `source` nomme l'origine dans les messages d'erreur
    (un agent, une entrée de registre) à la place du fichier d'un agent ; la mise
    en forme `(…)` attendue par `_valide` est posée ici, pour que l'appelant n'ait
    à donner qu'un libellé.
    """
    return _valide(serveur, contexte=f"({source})")


def _doublons(noms: Any) -> list[str]:
    """Les valeurs apparaissant plus d'une fois dans `noms`, triées (vide si aucune)."""
    vus = list(noms)
    return sorted({nom for nom in vus if vus.count(nom) > 1})


def _signature(serveur: ServeurMcp) -> str:
    """L'empreinte d'une déclaration : deux serveurs de même signature sont **la même**.

    C'est ce qui permet aux deux migrations de **mutualiser** — deux agents qui
    déclarent Figma à l'identique partagent une intégration au lieu d'en créer
    deux (#130), et migrer un second agent retrouve celle que le premier a posée
    (#263). La forme **stockée** et non la publique : celle-ci masque les
    littéraux, donc deux serveurs qui ne diffèrent que par une valeur en clair y
    seraient confondus.
    """
    return json.dumps(serveur.to_stored_dict(), sort_keys=True, ensure_ascii=False)


def _id_libre(base: str, pris: set[str]) -> str:
    """Un id d'intégration dérivé de `base`, unique parmi `pris` (suffixe `-2`, `-3`…)."""
    if base not in pris:
        return base
    n = 2
    while f"{base}-{n}" in pris:
        n += 1
    return f"{base}-{n}"


def _valide_integration(integration: IntegrationMcp) -> IntegrationMcp:
    """L'intégration telle quelle si elle est valide, sinon `ValueError` (id + déclaration)."""
    if not _ID_INTEGRATION.match(integration.id):
        raise ValueError(
            f"id d'intégration MCP invalide : {integration.id!r} (slug [a-z0-9_-] attendu)."
        )
    _valide(integration.serveur, contexte=f"(intégration {integration.id!r})")
    return integration


def _valide(serveur: ServeurMcp, *, contexte: str) -> ServeurMcp:
    """La déclaration `serveur` telle quelle si elle est valide, sinon `ValueError`.

    `contexte` localise la cause dans les messages (`(agent 'qa')`,
    `(intégration 'figma')`). Chaque forme est verrouillée sur son type : une
    commande locale (stdio) ne porte pas d'URL ni d'en-têtes, un endpoint
    distant (sse/http) ne porte ni commande ni environnement — une déclaration
    ambiguë est refusée plutôt qu'interprétée.
    """
    if not _NOM_SERVEUR.match(serveur.nom):
        raise ValueError(
            f"nom de serveur MCP invalide {contexte} : {serveur.nom!r} "
            "(slug [a-z0-9_-] attendu)."
        )
    ici = f"serveur MCP {serveur.nom!r} {contexte}"
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
