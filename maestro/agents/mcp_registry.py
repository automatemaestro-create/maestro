"""Registre **curé** de serveurs MCP — la bibliothèque recherchable (ticket #131, parent #129).

Aujourd'hui, brancher un serveur MCP sur un agent se fait à la main dans
`core/mcp/<agent>.json` (`maestro.agents.mcp`) : aucune découverte des
intégrations disponibles, config dupliquée quand deux agents partagent une
intégration. Ce module porte la **bibliothèque** : un registre de *templates*
de serveurs MCP curés, recherchable (« figma », « gitlab »…), chaque entrée
guidant sa configuration selon son mode d'auth ([docs/21](../../docs/21-configuration-mcp.md)).

Deux notions à ne pas confondre (parent #129) :

- une **entrée de registre** (`EntreeRegistre`) est un **template** : versionné,
  agnostique du fournisseur de modèle, il décrit *comment lancer* un serveur
  (transport + gabarit d'exécution `${VAR}`) et *comment l'authentifier* (mode
  d'auth, clés de secrets, procédure côté outil). Il ne porte aucun secret ;
- une **liaison** est l'instance d'un template pour un agent donné (lot 1 du
  parent, `maestro.agents.mcp.ServeurMcp`). Le passage template → liaison est
  l'**instanciation** (`RegistreMcp.instancier`).

**Garde-fou supply-chain** (modèle de menace
[docs/19](../../docs/19-securite-modele-de-menace.md)) :
*découverte ≠ installation*. Seule une entrée de l'**allowlist curée** est
instanciable — `instancier` refuse tout id inconnu du registre, jamais de
`npx -y <pkg arbitraire>`. L'allowlist *est* le registre : une intégration
n'existe pour Maestro que si elle a été curée ici, en clair, revue et versionnée.

Le format d'entrée réutilise la forme `server.json` du **registre MCP officiel**
(`registry.modelcontextprotocol.io` : nom/description + transport + gabarit
d'exécution) enrichie des métadonnées Maestro (mode d'auth, clés de secrets,
procédure d'émission). Le seed dérive des pilotes déjà versionnés dans
`core/mcp/` (#106/#105/#128), augmenté de la forge du projet (GitHub, #412).

Le registre est une **bibliothèque**, pas la configuration d'un agent : il porte
**GitHub et GitLab** côte à côte sans que ce soit une hésitation. Quelle forge ce
projet-ci utilise se lit dans `core/mcp/qa.json` — jamais ici.

Au POC le registre est un **seed en code** (`SEED`, versionné avec le dépôt) —
c'est cohérent avec « template versionné » et avec le garde-fou : l'allowlist
est revue en revue de code, pas éditée à chaud. En V1 il pourra passer en base
sans changer ce contrat (le même `RegistreMcp` au-dessus d'une autre source).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from maestro.agents.mcp import ServeurMcp, valide_serveur

#: Les trois modes d'authentification classés par la revue #126
#: ([docs/21](../../docs/21-configuration-mcp.md) §2) :
#: - `token_statique` : secret saisissable une fois (PAT GitLab, token de bot Slack) ;
#: - `appairage` : sans token, un identifiant éphémère renouvelé à chaque session
#:   (canal du pont Figma communautaire) — présenté comme non-secret ;
#: - `oauth_importe` : token OAuth émis par l'outil pour un client approuvé, que
#:   l'humain **importe** (Figma officiel) — expirable, renouvellement humain.
MODES_AUTH: tuple[str, ...] = ("token_statique", "appairage", "oauth_importe")


@dataclass(frozen=True)
class VariableSecret:
    """Une variable que l'humain doit fournir pour instancier un serveur curé.

    C'est le **sous-ensemble** des valeurs d'`env`/`headers` du gabarit qui
    portent une référence `${VAR}` (par opposition aux valeurs littérales de
    configuration, ex. `GITLAB_TOOLSETS=issues`, qui n'attendent aucune saisie).
    `secret` distingue un vrai secret (token, à chiffrer/masquer, #102/#132)
    d'un identifiant non sensible mais requis (ID d'espace de travail, canal).
    """

    cle: str
    description: str = ""
    secret: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Réémet la variable en dict JSON-sérialisable (aucune valeur — c'est un gabarit)."""
        return {"cle": self.cle, "description": self.description, "secret": self.secret}


@dataclass(frozen=True)
class EntreeRegistre:
    """Un template de serveur MCP curé — forme `server.json` + métadonnées Maestro.

    La partie *versionnable* du contrat : transport et gabarit d'exécution
    (`commande`/`args`/`env` pour un stdio, `url`/`headers` pour un endpoint
    distant, valeurs en `${VAR}` — jamais de secret en clair), plus ce dont une
    UI de configuration a besoin pour guider la saisie : `mode_auth` (docs/21),
    `secrets` (les variables à fournir) et `procedure_url` (le lien vers la
    procédure d'émission côté outil). `tags` alimente la recherche.

    `optionnel` se propage à la liaison instanciée (`ServeurMcp.optionnel`,
    #125) : une voie dont le secret manque est omise du montage sans faire
    échouer la tâche — le canal des capacités activées par un humain.
    """

    id: str
    nom: str
    description: str
    mode_auth: str
    transport: str
    commande: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    secrets: tuple[VariableSecret, ...] = ()
    procedure_url: str = ""
    optionnel: bool = False

    def vers_serveur(self, nom: str | None = None) -> ServeurMcp:
        """Instancie le template en `ServeurMcp` montable (gabarit `${VAR}` intact).

        Ne résout pas les références : le serveur produit est la forme
        *versionnable* (`${VAR}` en place) que `maestro.agents.mcp.resolus`
        montera plus tard. `nom` nomme la liaison (défaut : l'id du template) —
        c'est le préfixe des outils (`mcp__<nom>__…`). Le résultat est **validé**
        (`valide_serveur`) : une entrée mal formée est refusée avec sa cause,
        jamais instanciée à moitié.
        """
        serveur = ServeurMcp(
            nom=nom or self.id,
            type=self.transport,
            commande=self.commande,
            args=tuple(self.args),
            url=self.url,
            env=dict(self.env),
            headers=dict(self.headers),
            optionnel=self.optionnel,
        )
        return valide_serveur(serveur, source=f"registre MCP, entrée {self.id!r}")

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'entrée en dict JSON-sérialisable — la forme publique (API/UI).

        Le gabarit d'exécution est réémis **tel quel** : ses valeurs d'`env`/
        `headers` sont des références `${VAR}`, pas des secrets — c'est ce qu'une
        UI affiche pour guider la saisie. `curee: true` marque l'appartenance à
        l'allowlist (toute entrée servie par ce registre y est, par définition).
        """
        return {
            "id": self.id,
            "nom": self.nom,
            "description": self.description,
            "mode_auth": self.mode_auth,
            "transport": self.transport,
            "commande": self.commande,
            "args": list(self.args),
            "url": self.url,
            "env": dict(self.env),
            "headers": dict(self.headers),
            "tags": list(self.tags),
            "secrets": [s.to_dict() for s in self.secrets],
            "procedure_url": self.procedure_url,
            "optionnel": self.optionnel,
            "curee": True,
        }


class RegistreMcp:
    """La bibliothèque : des entrées curées, recherchables et **instanciables sous garde-fou**.

    `rechercher` filtre par nom/tag (recherche libre, insensible à la casse et
    aux accents) ; `get`/`lister` exposent les entrées ; `instancier` est la
    **seule** voie template → liaison, et elle applique le garde-fou
    supply-chain : un id absent de l'allowlist curée est refusé.

    Construit par défaut sur le seed en code (`RegistreMcp.curee()`) ; les tests
    (#134) et une V1 en base peuvent en injecter un autre — le contrat ne change
    pas.
    """

    def __init__(self, entrees: Iterable[EntreeRegistre]) -> None:
        index: dict[str, EntreeRegistre] = {}
        for entree in entrees:
            if entree.id in index:
                raise ValueError(f"entrée de registre MCP en double : {entree.id!r}.")
            if entree.mode_auth not in MODES_AUTH:
                raise ValueError(
                    f"mode d'auth invalide pour l'entrée {entree.id!r} : "
                    f"{entree.mode_auth!r} (attendu : {', '.join(MODES_AUTH)})."
                )
            # Toute entrée curée doit être instanciable : on valide le gabarit
            # dès la construction, jamais un registre à moitié bon.
            entree.vers_serveur()
            index[entree.id] = entree
        self._entrees = index

    @classmethod
    def curee(cls) -> RegistreMcp:
        """Le registre curé du POC : le seed en code (GitHub, GitLab, Slack, Figma officiel)."""
        return cls(SEED)

    def lister(self) -> tuple[EntreeRegistre, ...]:
        """Toutes les entrées curées, dans l'ordre du seed (stable)."""
        return tuple(self._entrees.values())

    def get(self, id: str) -> EntreeRegistre | None:
        """L'entrée d'id `id`, ou None si elle n'est pas dans l'allowlist curée."""
        return self._entrees.get(id)

    def rechercher(self, requete: str = "") -> tuple[EntreeRegistre, ...]:
        """Les entrées dont le nom, l'id, la description ou un tag contient `requete`.

        Recherche libre, insensible à la casse et aux accents ; une requête vide
        rend toutes les entrées. L'ordre du seed est conservé — pas de tri par
        pertinence au POC, le registre est petit.
        """
        besoin = _normalise(requete)
        if not besoin:
            return self.lister()
        return tuple(e for e in self._entrees.values() if besoin in _foin(e))

    def instancier(self, id: str, *, nom: str | None = None) -> ServeurMcp:
        """Instancie l'entrée curée `id` en `ServeurMcp` montable — **garde-fou supply-chain**.

        Seule une entrée de l'allowlist curée est instanciable (docs/19,
        découverte ≠ installation) : un `id` inconnu du registre lève
        `ValueError` sans rien monter. C'est l'unique voie template → liaison ;
        le montage effectif (résolution des `${VAR}`) reste le rôle de
        `maestro.agents.mcp.resolus`.
        """
        entree = self._entrees.get(id)
        if entree is None:
            raise ValueError(
                f"serveur MCP {id!r} hors allowlist curée : non instanciable "
                "(découverte ≠ installation — un serveur doit être curé dans le "
                "registre avant d'être monté ; voir docs/19)."
            )
        return entree.vers_serveur(nom=nom)


def _normalise(texte: str) -> str:
    """`texte` replié pour la recherche : sans casse ni accents (NFKD, casefold)."""
    decompose = unicodedata.normalize("NFKD", texte)
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return sans_accent.casefold().strip()


def _foin(entree: EntreeRegistre) -> str:
    """La botte de foin d'une entrée : id, nom, description et tags, normalisés."""
    return _normalise(" ".join((entree.id, entree.nom, entree.description, *entree.tags)))


def _secrets(*variables: tuple[str, str, bool]) -> tuple[VariableSecret, ...]:
    """Petit constructeur du seed : `(clé, description, secret)` → `VariableSecret`."""
    return tuple(VariableSecret(cle, description, secret) for cle, description, secret in variables)


#: Le seed curé du POC — dérivé des pilotes déjà versionnés dans `core/mcp/`
#: (forge #412 → `qa.json`, Slack #105 → `devops.json`, Figma officiel #128 →
#: `designer.json`). Chaque entrée porte transport, gabarit `${VAR}`, mode
#: d'auth (docs/21) et lien de procédure côté outil. **Cette liste EST
#: l'allowlist** : y ajouter une intégration est un geste de revue de code.
#:
#: ⚠ **Deux forges y figurent, et ce n'est pas une hésitation** (#412). Le
#: registre est une **bibliothèque** (#131), pas la configuration d'un agent :
#: il répond à « quelles intégrations existe-t-il ? », jamais à « laquelle ce
#: projet utilise-t-il ? ». Le **défaut du produit** est GitHub et se lit dans
#: `core/mcp/qa.json` seul ; `gitlab` reste curé parce qu'un projet outillé par
#: Maestro n'est pas forcément sur la forge du nôtre — et l'en retirer
#: interdirait de le monter (l'allowlist *est* le registre).
SEED: tuple[EntreeRegistre, ...] = (
    EntreeRegistre(
        id="github",
        nom="GitHub",
        description="Lecture/écriture des tickets et Pull Requests GitHub — la forge du projet.",
        mode_auth="token_statique",
        transport="http",
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": "Bearer ${GITHUB_TOKEN}"},
        tags=("tickets", "issues", "pull-request", "devops", "scm", "forge"),
        secrets=_secrets(
            (
                "GITHUB_TOKEN",
                "PAT GitHub à portée restreinte (Issues + Pull requests du seul dépôt "
                "du projet) — c'est le jeton, et non la config du serveur, qui borne "
                "le périmètre",
                True,
            ),
        ),
        procedure_url="core/mcp/README.md#obtention-du-token-github",
        # Aucun poste ne porte encore `GITHUB_TOKEN` (l'outillage s'authentifie
        # par `gh`, pas par ce fichier) : non optionnel, la bascule ferait
        # échouer toute exécution outillée du QA au premier run. Canal #125 —
        # sans jeton, la voie est omise du montage, sans échec.
        optionnel=True,
    ),
    EntreeRegistre(
        id="gitlab",
        nom="GitLab",
        description="Lecture/écriture des tickets et Merge Requests GitLab.",
        mode_auth="token_statique",
        transport="stdio",
        commande="npx",
        args=("-y", "@zereight/mcp-gitlab"),
        env={
            "GITLAB_PERSONAL_ACCESS_TOKEN": "${GITLAB_TOKEN}",
            "GITLAB_TOOLSETS": "issues",
            "GITLAB_PERMISSION_MODE": "modify",
        },
        tags=("tickets", "issues", "merge-request", "devops", "scm", "forge"),
        secrets=_secrets(
            ("GITLAB_TOKEN", "PAT GitLab (glpat-…), scope api, créé dans l'UI GitLab", True),
        ),
        procedure_url="docs/16-pilote-mcp-tickets-gitlab.md#23-obtention-du-token",
    ),
    EntreeRegistre(
        id="slack",
        nom="Slack",
        description="Publication et lecture de messages dans les canaux Slack.",
        mode_auth="token_statique",
        transport="stdio",
        commande="npx",
        args=("-y", "@modelcontextprotocol/server-slack"),
        env={
            "SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}",
            "SLACK_TEAM_ID": "${SLACK_TEAM_ID}",
        },
        tags=("messagerie", "notifications", "canaux", "devops", "chat"),
        secrets=_secrets(
            (
                "SLACK_BOT_TOKEN",
                "Bot User OAuth Token (xoxb-…), scopes chat:write + channels:read",
                True,
            ),
            ("SLACK_TEAM_ID", "ID de l'espace de travail Slack (non secret, mais requis)", False),
        ),
        procedure_url="docs/15-pilote-mcp-slack.md#2-installation-de-lapp",
    ),
    EntreeRegistre(
        id="figma-officiel",
        nom="Figma (serveur officiel)",
        description="Contexte de design Figma via le serveur MCP officiel (OAuth verrouillé).",
        mode_auth="oauth_importe",
        transport="http",
        url="https://mcp.figma.com/mcp",
        headers={"Authorization": "Bearer ${FIGMA_OAUTH_TOKEN}"},
        tags=("design", "figma", "ui", "maquettes"),
        secrets=_secrets(
            (
                "FIGMA_OAUTH_TOKEN",
                "Token OAuth mcp:connect importé d'un client approuvé (Claude Code…) — expirable",
                True,
            ),
        ),
        procedure_url="docs/20-pilote-mcp-figma.md#6-le-serveur-mcp-officiel-figma",
        optionnel=True,
    ),
)
