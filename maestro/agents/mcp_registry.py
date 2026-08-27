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

**Élargissement (#271, [docs/21 §3.4](../../docs/21-configuration-mcp.md)).** Le
seed d'origine tenait en quatre entrées — les pilotes déjà versionnés dans
`core/mcp/`. Assez pour prouver le mécanisme, trop étroit pour ce qu'une
bibliothèque promet : on n'y découvrait rien. Il couvre désormais les serveurs
les plus utilisés de l'écosystème, chacun avec son `editeur`, son mode d'auth et
ce qu'il apporte ; `PROVENANCE` dit d'où vient la liste et quand elle a été
revue, `popularite` met les plus courants en tête (`USAGE_*`).

⚠ **La règle de curation est une règle de sécurité, pas de style** : un gabarit
qu'on ne sait pas écrire **exactement** n'entre pas. Écrire un `npx -y <paquet>`
de mémoire, c'est écrire une invitation au typosquatting dans une allowlist —
l'inverse exact de ce que docs/19 protège ici. D'où la préférence pour les
endpoints HTTP officiels (rien à exécuter, l'URL est vérifiable) et pour les
paquets attestés par une source. Corollaire à connaître avant d'ajouter une
entrée : `maestro.agents.mcp.resolus` ne résout les `${VAR}` que dans `env` et
`headers`, **jamais dans `args`** — un serveur dont le paramètre est un argument
de ligne de commande (`filesystem`, `postgres`) n'est donc pas gabaritable ici,
et il est écarté plutôt que déclaré à moitié.
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
#:
#: …plus un quatrième, ajouté par l'élargissement du registre (#271) :
#: - `sans_secret` : le serveur n'émet **aucun** secret — un utilitaire local
#:   (`fetch`, `memory`, Playwright…) qu'on lance tel quel.
#:
#: ⚠ Ce n'est **pas un quatrième parcours de saisie**, et docs/21 §2 garde donc
#: raison de dire que la classification « n'a pas bougé » : les trois modes
#: classent *comment un secret s'obtient*, question qui n'a pas d'objet quand il
#: n'y en a aucun. `sans_secret` est le **cas dégénéré** de la classification,
#: pas une extension de sa règle — sans lui, la moitié des serveurs les plus
#: utilisés de l'écosystème seraient inexprimables ici, donc absents de la
#: bibliothèque. Il reste porté par `mode_auth` (et non par un booléen à côté)
#: pour que l'UI n'ait **qu'un** champ à regarder pour choisir son formulaire.
MODES_AUTH: tuple[str, ...] = (
    "token_statique",
    "appairage",
    "oauth_importe",
    "sans_secret",
)


#: Les paliers d'usage (#271) : le repère qui met les intégrations les plus
#: courantes en tête de la bibliothèque (`EntreeRegistre.popularite`).
#:
#: Ce sont des **paliers** et non un classement au rang près, parce que c'est
#: tout ce qu'une liste curée peut honnêtement porter : les annuaires publics de
#: l'écosystème s'accordent sur l'ordre de grandeur (« tout le monde branche sa
#: forge », « peu de monde branche PagerDuty ») et pas sur un rang. Quatre
#: valeurs espacées, pour qu'un ajout n'oblige jamais à renuméroter ses voisins ;
#: à palier égal l'ordre est alphabétique, donc stable et sans faux gagnant.
USAGE_INCONTOURNABLE = 90
USAGE_TRES_COURANT = 70
USAGE_COURANT = 50
USAGE_SPECIALISE = 30

#: L'id réservé par la route `GET /api/mcp/registre/provenance`
#: (`maestro.controltower.app`) : aucune entrée ne peut le porter, sans quoi
#: elle deviendrait injoignable par `GET /api/mcp/registre/{id}`. Le registre
#: refuse cet id à la construction — la route n'a donc pas à parier sur l'ordre
#: de déclaration de ses voisines.
ID_RESERVES: frozenset[str] = frozenset({"provenance"})


@dataclass(frozen=True)
class SourceCitee:
    """Une source de la curation : d'où vient une entrée, et où la revérifier."""

    libelle: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        """Réémet la source en dict JSON-sérialisable."""
        return {"libelle": self.libelle, "url": self.url}


@dataclass(frozen=True)
class Provenance:
    """D'où vient cette liste, et quand elle a été revue — **dit à l'écran** (#271).

    Un registre curé sans provenance affichée demande une confiance qu'il ne
    justifie pas : « les plus utilisés » selon qui, et à quelle date ? La
    bibliothèque porte donc, visible dans l'UI, ses sources et la date de sa
    dernière revue. `revue_le` est une date ISO — celle de la **revue humaine**
    (une revue de code : le seed est versionné), jamais un horodatage de build.
    """

    resume: str
    sources: tuple[SourceCitee, ...]
    revue_le: str

    def to_dict(self) -> dict[str, Any]:
        """Réémet la provenance en dict JSON-sérialisable (forme publique API/UI)."""
        return {
            "resume": self.resume,
            "sources": [s.to_dict() for s in self.sources],
            "revue_le": self.revue_le,
        }


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

    `editeur` et `popularite` datent de l'élargissement (#271) : le premier dit
    **qui publie** le serveur (une intégration se choisit autant sur son éditeur
    que sur son nom — et c'est ce qui distingue le serveur officiel d'un pont
    communautaire), le second est le repère d'usage qui met les plus courants en
    tête (`USAGE_*`). Tous deux ont un défaut vide/nul : une entrée injectée par
    un test reste valide sans les porter.
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
    editeur: str = ""
    popularite: int = 0

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
            "editeur": self.editeur,
            "popularite": self.popularite,
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

    def __init__(
        self,
        entrees: Iterable[EntreeRegistre],
        provenance: Provenance | None = None,
    ) -> None:
        index: dict[str, EntreeRegistre] = {}
        for entree in entrees:
            if entree.id in index:
                raise ValueError(f"entrée de registre MCP en double : {entree.id!r}.")
            if entree.id in ID_RESERVES:
                raise ValueError(
                    f"id de registre MCP réservé : {entree.id!r} — il est pris par une "
                    "route de l'API (l'entrée serait injoignable)."
                )
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
        self.provenance = provenance or PROVENANCE

    @classmethod
    def curee(cls) -> RegistreMcp:
        """Le registre curé : le seed en code (`SEED`) et la provenance qui le date."""
        return cls(SEED, PROVENANCE)

    def lister(self) -> tuple[EntreeRegistre, ...]:
        """Toutes les entrées curées, **les plus courantes d'abord** (`_rang`)."""
        return tuple(sorted(self._entrees.values(), key=_rang))

    def get(self, id: str) -> EntreeRegistre | None:
        """L'entrée d'id `id`, ou None si elle n'est pas dans l'allowlist curée."""
        return self._entrees.get(id)

    def rechercher(self, requete: str = "") -> tuple[EntreeRegistre, ...]:
        """Les entrées dont le nom, l'éditeur, un tag (ou l'id/la description) porte `requete`.

        Recherche libre, insensible à la casse et aux accents ; une requête vide
        rend tout le registre. Le résultat est trié **les plus courants d'abord**
        (#271) : sur une bibliothèque de plusieurs dizaines d'entrées, l'ordre de
        déclaration ne veut plus rien dire pour qui cherche « base de données ».
        Id et description restent dans la botte de foin bien que le critère ne
        nomme que nom/éditeur/tags : c'est un sur-ensemble, et le retirer ferait
        échouer des recherches qui marchent (« tickets » vit dans les tags, mais
        « merge request » vit dans une description).
        """
        besoin = _normalise(requete)
        if not besoin:
            return self.lister()
        trouvees = (e for e in self._entrees.values() if besoin in _foin(e))
        return tuple(sorted(trouvees, key=_rang))

    def tags(self) -> tuple[str, ...]:
        """Tous les tags du registre, dédoublonnés et triés — les pistes de recherche.

        Ce que l'UI propose quand une recherche ne rend rien (#271) : un
        cul-de-sac se sort en montrant *par quoi* on peut chercher, jamais en
        répétant que la requête est vide de résultats.
        """
        return tuple(sorted({tag for e in self._entrees.values() for tag in e.tags}))

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
    """La botte de foin d'une entrée : id, nom, éditeur, description et tags, normalisés."""
    return _normalise(
        " ".join((entree.id, entree.nom, entree.editeur, entree.description, *entree.tags))
    )


def _rang(entree: EntreeRegistre) -> tuple[int, str]:
    """La clé de tri : palier d'usage décroissant, puis nom (stable, sans faux gagnant)."""
    return (-entree.popularite, _normalise(entree.nom))


def _secrets(*variables: tuple[str, str, bool]) -> tuple[VariableSecret, ...]:
    """Petit constructeur du seed : `(clé, description, secret)` → `VariableSecret`."""
    return tuple(VariableSecret(cle, description, secret) for cle, description, secret in variables)


#: D'où vient cette liste, et quand elle a été revue (#271) — **affiché** au pied
#: de la bibliothèque, servi par `GET /api/mcp/registre/provenance`.
#:
#: ⚠ `revue_le` se met à jour **avec le seed**, dans le même commit : une date
#: qui ne bouge pas quand la liste bouge est pire qu'une date absente, elle
#: atteste une fraîcheur que personne n'a vérifiée.
PROVENANCE = Provenance(
    resume=(
        "Sélection curée à la main parmi les serveurs MCP les plus utilisés de "
        "l'écosystème, d'après les annuaires publics ci-dessous — jamais "
        "moissonnée : chaque entrée est écrite, relue en revue de code et "
        "versionnée avec le dépôt."
    ),
    sources=(
        SourceCitee(
            libelle="Serveurs de référence et intégrations officielles (dépôt MCP)",
            url="https://github.com/modelcontextprotocol/servers",
        ),
        SourceCitee(
            libelle="Registre MCP officiel",
            url="https://registry.modelcontextprotocol.io",
        ),
        SourceCitee(
            libelle="Annuaire communautaire awesome-mcp-servers",
            url="https://github.com/punkpeye/awesome-mcp-servers",
        ),
    ),
    revue_le="2026-08-28",
)

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
#:
#: ⚠ **L'ordre de déclaration ci-dessous ne veut plus rien dire** depuis #271 :
#: `lister`/`rechercher` trient par palier d'usage puis par nom. Il ne reste
#: qu'un ordre de **lecture**, groupé par famille — un test qui épinglerait une
#: position épinglerait donc le tri, pas le seed.
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
        editeur="GitHub",
        popularite=USAGE_INCONTOURNABLE,
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
        editeur="zereight (communauté)",
        popularite=USAGE_TRES_COURANT,
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
        editeur="Serveur de référence MCP",
        popularite=USAGE_INCONTOURNABLE,
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
        editeur="Figma",
        popularite=USAGE_COURANT,
    ),
    # ── Figma, l'autre voie (#271) ────────────────────────────────────────────
    # Le pont communautaire de docs/21 §2 : le **seul** mode `appairage` du
    # registre, qui n'était jusqu'ici représenté par aucune entrée alors que la
    # classification le décrit. Il a quitté `core/mcp/designer.json` au profit du
    # serveur officiel (docs/20 §6) — le curer ici n'est pas un retour en
    # arrière : le registre répond à « quelles intégrations existe-t-il ? »,
    # jamais à « laquelle ce projet utilise-t-il ? » (même raison que les deux
    # forges ci-dessus).
    EntreeRegistre(
        id="figma-pont",
        nom="Figma (pont communautaire)",
        description=(
            "Agir dans Figma avec la session de l'utilisateur, via le plugin compagnon — "
            "sans token : le plugin affiche un canal d'appairage, valable le temps de la session."
        ),
        mode_auth="appairage",
        transport="stdio",
        commande="npx",
        args=("-y", "cursor-talk-to-figma-mcp@0.3.5"),
        env={"FIGMA_CHANNEL": "${FIGMA_CHANNEL}"},
        tags=("design", "figma", "ui", "maquettes", "appairage"),
        secrets=_secrets(
            (
                "FIGMA_CHANNEL",
                "Canal affiché par le plugin « Talk To Figma MCP » — jetable, "
                "renouvelé à chaque session (aucun token d'API n'existe)",
                False,
            ),
        ),
        procedure_url="docs/20-pilote-mcp-figma.md#11-architecture-dappairage",
        optionnel=True,
        editeur="sonnylazuardi (communauté)",
        popularite=USAGE_SPECIALISE,
    ),
    # ── Tickets, projet et base de connaissances ──────────────────────────────
    EntreeRegistre(
        id="linear",
        nom="Linear",
        description=(
            "Tickets, cycles et projets Linear : lire le backlog, créer et déplacer des issues."
        ),
        mode_auth="oauth_importe",
        transport="http",
        url="https://mcp.linear.app/mcp",
        headers={"Authorization": "Bearer ${LINEAR_OAUTH_TOKEN}"},
        tags=("tickets", "issues", "projet", "roadmap", "agile", "backlog"),
        secrets=_secrets(
            (
                "LINEAR_OAUTH_TOKEN",
                "Token OAuth importé d'un client approuvé — expirable, renouvellement humain",
                True,
            ),
        ),
        procedure_url="https://linear.app/docs/mcp",
        optionnel=True,
        editeur="Linear",
        popularite=USAGE_TRES_COURANT,
    ),
    EntreeRegistre(
        id="atlassian",
        nom="Atlassian (Jira & Confluence)",
        description=(
            "Tickets Jira et pages Confluence : suivre un sprint, lire une spec, commenter."
        ),
        mode_auth="oauth_importe",
        transport="sse",
        url="https://mcp.atlassian.com/v1/sse",
        headers={"Authorization": "Bearer ${ATLASSIAN_OAUTH_TOKEN}"},
        tags=("tickets", "jira", "confluence", "wiki", "projet", "agile"),
        secrets=_secrets(
            (
                "ATLASSIAN_OAUTH_TOKEN",
                "Token OAuth importé d'un client approuvé — expirable, renouvellement humain",
                True,
            ),
        ),
        procedure_url="https://support.atlassian.com/rovo/docs/getting-started-with-the-atlassian-remote-mcp-server/",
        optionnel=True,
        editeur="Atlassian",
        popularite=USAGE_TRES_COURANT,
    ),
    EntreeRegistre(
        id="notion",
        nom="Notion",
        description=(
            "Pages et bases Notion : chercher dans la doc interne, lire et écrire une page."
        ),
        mode_auth="oauth_importe",
        transport="http",
        url="https://mcp.notion.com/mcp",
        headers={"Authorization": "Bearer ${NOTION_OAUTH_TOKEN}"},
        tags=("notes", "wiki", "documentation", "base-de-connaissances", "projet"),
        secrets=_secrets(
            (
                "NOTION_OAUTH_TOKEN",
                "Token OAuth importé d'un client approuvé — expirable, renouvellement humain",
                True,
            ),
        ),
        procedure_url="https://developers.notion.com/docs/mcp",
        optionnel=True,
        editeur="Notion",
        popularite=USAGE_TRES_COURANT,
    ),
    EntreeRegistre(
        id="asana",
        nom="Asana",
        description=(
            "Tâches et projets Asana : état d'un projet, création et affectation de tâches."
        ),
        mode_auth="oauth_importe",
        transport="sse",
        url="https://mcp.asana.com/sse",
        headers={"Authorization": "Bearer ${ASANA_OAUTH_TOKEN}"},
        tags=("taches", "projet", "planning", "collaboration"),
        secrets=_secrets(
            (
                "ASANA_OAUTH_TOKEN",
                "Token OAuth importé d'un client approuvé — expirable, renouvellement humain",
                True,
            ),
        ),
        procedure_url="https://developers.asana.com/docs/using-asanas-mcp-server",
        optionnel=True,
        editeur="Asana",
        popularite=USAGE_COURANT,
    ),
    # ── Observabilité et incidents ────────────────────────────────────────────
    EntreeRegistre(
        id="sentry",
        nom="Sentry",
        description=(
            "Erreurs et traces Sentry : ouvrir un incident, lire une stack, "
            "relier un crash à un déploiement."
        ),
        mode_auth="oauth_importe",
        transport="http",
        url="https://mcp.sentry.dev/mcp",
        headers={"Authorization": "Bearer ${SENTRY_OAUTH_TOKEN}"},
        tags=("erreurs", "observabilite", "monitoring", "incidents", "devops"),
        secrets=_secrets(
            (
                "SENTRY_OAUTH_TOKEN",
                "Token OAuth importé d'un client approuvé — expirable, renouvellement humain",
                True,
            ),
        ),
        procedure_url="https://docs.sentry.io/product/sentry-mcp/",
        optionnel=True,
        editeur="Sentry",
        popularite=USAGE_COURANT,
    ),
    # ── Données et plateformes applicatives ───────────────────────────────────
    EntreeRegistre(
        id="supabase",
        nom="Supabase",
        description="Projets Supabase : interroger la base, lire le schéma, gérer les migrations.",
        mode_auth="token_statique",
        transport="stdio",
        commande="npx",
        args=("-y", "@supabase/mcp-server-supabase@latest"),
        env={"SUPABASE_ACCESS_TOKEN": "${SUPABASE_ACCESS_TOKEN}"},
        tags=("base-de-donnees", "postgres", "backend", "sql", "donnees"),
        secrets=_secrets(
            (
                "SUPABASE_ACCESS_TOKEN",
                "Jeton d'accès personnel Supabase (Account → Access Tokens)",
                True,
            ),
        ),
        procedure_url="https://supabase.com/docs/guides/getting-started/mcp",
        optionnel=True,
        editeur="Supabase",
        popularite=USAGE_COURANT,
    ),
    EntreeRegistre(
        id="stripe",
        nom="Stripe",
        description=(
            "Paiements Stripe : clients, abonnements et factures, en lecture comme en écriture."
        ),
        mode_auth="token_statique",
        transport="http",
        url="https://mcp.stripe.com",
        headers={"Authorization": "Bearer ${STRIPE_SECRET_KEY}"},
        tags=("paiement", "facturation", "abonnements", "finance"),
        secrets=_secrets(
            (
                "STRIPE_SECRET_KEY",
                "Clé API Stripe **restreinte** (rk_…) — c'est la clé, et non le serveur, "
                "qui borne ce que l'agent peut faire",
                True,
            ),
        ),
        procedure_url="https://docs.stripe.com/mcp",
        optionnel=True,
        editeur="Stripe",
        popularite=USAGE_COURANT,
    ),
    EntreeRegistre(
        id="neon",
        nom="Neon",
        description=(
            "Bases Postgres Neon : brancher une base, jouer une requête, "
            "gérer les branches de données."
        ),
        mode_auth="oauth_importe",
        transport="http",
        url="https://mcp.neon.tech/mcp",
        headers={"Authorization": "Bearer ${NEON_OAUTH_TOKEN}"},
        tags=("base-de-donnees", "postgres", "sql", "donnees", "serverless"),
        secrets=_secrets(
            (
                "NEON_OAUTH_TOKEN",
                "Token OAuth importé d'un client approuvé — expirable, renouvellement humain",
                True,
            ),
        ),
        procedure_url="https://neon.com/docs/ai/neon-mcp-server",
        optionnel=True,
        editeur="Neon",
        popularite=USAGE_SPECIALISE,
    ),
    # ── Déploiement et infrastructure ─────────────────────────────────────────
    EntreeRegistre(
        id="vercel",
        nom="Vercel",
        description=(
            "Déploiements Vercel : état d'un déploiement, journaux d'exécution, "
            "projets et domaines."
        ),
        mode_auth="oauth_importe",
        transport="http",
        url="https://mcp.vercel.com",
        headers={"Authorization": "Bearer ${VERCEL_OAUTH_TOKEN}"},
        tags=("deploiement", "hebergement", "frontend", "devops", "journaux"),
        secrets=_secrets(
            (
                "VERCEL_OAUTH_TOKEN",
                "Token OAuth importé d'un client approuvé — expirable, renouvellement humain",
                True,
            ),
        ),
        procedure_url="https://vercel.com/docs/mcp/vercel-mcp",
        optionnel=True,
        editeur="Vercel",
        popularite=USAGE_COURANT,
    ),
    EntreeRegistre(
        id="cloudflare-docs",
        nom="Cloudflare (documentation)",
        description=(
            "La documentation Cloudflare à jour, interrogeable — endpoint public : "
            "aucun compte, aucun secret, rien de votre infrastructure n'y transite."
        ),
        mode_auth="sans_secret",
        transport="sse",
        url="https://docs.mcp.cloudflare.com/sse",
        tags=("documentation", "reference", "cloud", "edge", "devops"),
        procedure_url="https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/",
        editeur="Cloudflare",
        popularite=USAGE_SPECIALISE,
    ),
    # ── Recherche web, documentation et collecte ──────────────────────────────
    EntreeRegistre(
        id="context7",
        nom="Context7",
        description=(
            "La documentation à jour d'une bibliothèque, injectée dans le contexte — "
            "l'antidote au code écrit d'après une version périmée."
        ),
        mode_auth="sans_secret",
        transport="stdio",
        commande="npx",
        args=("-y", "@upstash/context7-mcp"),
        tags=("documentation", "reference", "bibliotheques", "api", "veille"),
        procedure_url="https://github.com/upstash/context7",
        editeur="Upstash",
        popularite=USAGE_TRES_COURANT,
    ),
    EntreeRegistre(
        id="fetch",
        nom="Fetch (page web)",
        description=(
            "Récupérer une page web et la rendre en markdown lisible par un agent. "
            "Exige `uv` sur le poste (`uvx`)."
        ),
        mode_auth="sans_secret",
        transport="stdio",
        commande="uvx",
        args=("mcp-server-fetch",),
        tags=("web", "http", "scraping", "lecture", "markdown"),
        procedure_url="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        editeur="Serveur de référence MCP",
        popularite=USAGE_TRES_COURANT,
    ),
    EntreeRegistre(
        id="brave-search",
        nom="Brave Search",
        description=(
            "Recherche web et locale via l'API Brave — résultats frais, hors index d'un modèle."
        ),
        mode_auth="token_statique",
        transport="stdio",
        commande="npx",
        args=("-y", "@modelcontextprotocol/server-brave-search"),
        env={"BRAVE_API_KEY": "${BRAVE_API_KEY}"},
        tags=("recherche", "web", "veille", "actualites"),
        secrets=_secrets(
            ("BRAVE_API_KEY", "Clé de l'API Brave Search (offre gratuite disponible)", True),
        ),
        procedure_url="https://brave.com/search/api/",
        optionnel=True,
        editeur="Serveur de référence MCP",
        popularite=USAGE_COURANT,
    ),
    EntreeRegistre(
        id="tavily",
        nom="Tavily",
        description=(
            "Recherche web pensée pour les agents : réponses sourcées et extraction de contenu."
        ),
        mode_auth="token_statique",
        transport="stdio",
        commande="npx",
        args=("-y", "tavily-mcp"),
        env={"TAVILY_API_KEY": "${TAVILY_API_KEY}"},
        tags=("recherche", "web", "veille", "sources", "extraction"),
        secrets=_secrets(("TAVILY_API_KEY", "Clé de l'API Tavily (tvly-…)", True)),
        procedure_url="https://docs.tavily.com/documentation/mcp",
        optionnel=True,
        editeur="Tavily",
        popularite=USAGE_COURANT,
    ),
    EntreeRegistre(
        id="exa",
        nom="Exa",
        description="Recherche sémantique sur le web et sur des corpus de code, avec extraits.",
        mode_auth="token_statique",
        transport="stdio",
        commande="npx",
        args=("-y", "exa-mcp-server"),
        env={"EXA_API_KEY": "${EXA_API_KEY}"},
        tags=("recherche", "web", "semantique", "veille"),
        secrets=_secrets(("EXA_API_KEY", "Clé de l'API Exa", True)),
        procedure_url="https://docs.exa.ai/reference/exa-mcp",
        optionnel=True,
        editeur="Exa",
        popularite=USAGE_SPECIALISE,
    ),
    EntreeRegistre(
        id="firecrawl",
        nom="Firecrawl",
        description=(
            "Explorer un site entier et le rendre en markdown structuré — "
            "au-delà d'une page isolée."
        ),
        mode_auth="token_statique",
        transport="stdio",
        commande="npx",
        args=("-y", "firecrawl-mcp"),
        env={"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"},
        tags=("web", "scraping", "crawl", "extraction", "markdown"),
        secrets=_secrets(("FIRECRAWL_API_KEY", "Clé de l'API Firecrawl (fc-…)", True)),
        procedure_url="https://docs.firecrawl.dev/mcp-server",
        optionnel=True,
        editeur="Firecrawl",
        popularite=USAGE_COURANT,
    ),
    EntreeRegistre(
        id="deepwiki",
        nom="DeepWiki",
        description=(
            "Poser une question sur un dépôt GitHub public et recevoir une réponse "
            "documentée — endpoint public, aucun secret."
        ),
        mode_auth="sans_secret",
        transport="http",
        url="https://mcp.deepwiki.com/mcp",
        tags=("documentation", "code", "reference", "github", "lecture"),
        procedure_url="https://docs.devin.ai/work-with-devin/deepwiki-mcp",
        editeur="Cognition (Devin)",
        popularite=USAGE_SPECIALISE,
    ),
    EntreeRegistre(
        id="hugging-face",
        nom="Hugging Face",
        description=(
            "Modèles, jeux de données et Spaces du Hub : chercher, lire une fiche, explorer."
        ),
        mode_auth="token_statique",
        transport="http",
        url="https://huggingface.co/mcp",
        headers={"Authorization": "Bearer ${HF_TOKEN}"},
        tags=("modeles", "datasets", "ia", "recherche", "hub"),
        secrets=_secrets(
            ("HF_TOKEN", "Jeton d'accès Hugging Face (Settings → Access Tokens)", True),
        ),
        procedure_url="https://huggingface.co/settings/mcp",
        optionnel=True,
        editeur="Hugging Face",
        popularite=USAGE_SPECIALISE,
    ),
    # ── Navigateur, cartes et utilitaires locaux ──────────────────────────────
    EntreeRegistre(
        id="playwright",
        nom="Playwright",
        description=(
            "Piloter un vrai navigateur : naviguer, remplir, cliquer, capturer — "
            "sur l'arbre d'accessibilité plutôt que sur des pixels. "
            "C'est le serveur derrière `chrome-maestro` dans ce dépôt."
        ),
        mode_auth="sans_secret",
        transport="stdio",
        commande="npx",
        args=("-y", "@playwright/mcp@latest"),
        tags=("navigateur", "web", "tests", "captures", "automatisation", "qa"),
        procedure_url="scripts/mcp/playwright-mcp.mjs",
        editeur="Microsoft",
        popularite=USAGE_INCONTOURNABLE,
    ),
    EntreeRegistre(
        id="google-maps",
        nom="Google Maps",
        description="Géocodage, itinéraires et lieux : convertir une adresse, calculer un trajet.",
        mode_auth="token_statique",
        transport="stdio",
        commande="npx",
        args=("-y", "@modelcontextprotocol/server-google-maps"),
        env={"GOOGLE_MAPS_API_KEY": "${GOOGLE_MAPS_API_KEY}"},
        tags=("cartes", "geocodage", "itineraires", "lieux"),
        secrets=_secrets(
            ("GOOGLE_MAPS_API_KEY", "Clé d'API Google Maps Platform (console Google Cloud)", True),
        ),
        procedure_url="https://developers.google.com/maps/documentation/javascript/get-api-key",
        optionnel=True,
        editeur="Serveur de référence MCP",
        popularite=USAGE_SPECIALISE,
    ),
    EntreeRegistre(
        id="memory",
        nom="Mémoire (graphe de connaissances)",
        description=(
            "Une mémoire persistante entre sessions, sous forme de graphe "
            "d'entités et de relations — ce qu'un agent doit se rappeler d'une fois sur l'autre."
        ),
        mode_auth="sans_secret",
        transport="stdio",
        commande="npx",
        args=("-y", "@modelcontextprotocol/server-memory"),
        tags=("memoire", "graphe", "connaissances", "persistance", "contexte"),
        procedure_url="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        editeur="Serveur de référence MCP",
        popularite=USAGE_TRES_COURANT,
    ),
    EntreeRegistre(
        id="sequential-thinking",
        nom="Raisonnement séquentiel",
        description=(
            "Décomposer un problème en étapes révisables, et permettre à l'agent "
            "de revenir sur une branche."
        ),
        mode_auth="sans_secret",
        transport="stdio",
        commande="npx",
        args=("-y", "@modelcontextprotocol/server-sequential-thinking"),
        tags=("raisonnement", "planification", "reflexion", "methode"),
        procedure_url="https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
        editeur="Serveur de référence MCP",
        popularite=USAGE_COURANT,
    ),
    EntreeRegistre(
        id="git",
        nom="Git (dépôt local)",
        description=(
            "Lire et manipuler un dépôt Git local : historique, diff, branches. "
            "Le dépôt visé est un paramètre d'outil. Exige `uv` sur le poste (`uvx`)."
        ),
        mode_auth="sans_secret",
        transport="stdio",
        commande="uvx",
        args=("mcp-server-git",),
        tags=("git", "scm", "historique", "diff", "code"),
        procedure_url="https://github.com/modelcontextprotocol/servers/tree/main/src/git",
        editeur="Serveur de référence MCP",
        popularite=USAGE_COURANT,
    ),
    EntreeRegistre(
        id="time",
        nom="Temps et fuseaux",
        description=(
            "L'heure courante et les conversions de fuseau — ce qu'un modèle ne "
            "peut pas savoir seul. Exige `uv` sur le poste (`uvx`)."
        ),
        mode_auth="sans_secret",
        transport="stdio",
        commande="uvx",
        args=("mcp-server-time",),
        tags=("temps", "horloge", "fuseaux", "dates", "utilitaire"),
        procedure_url="https://github.com/modelcontextprotocol/servers/tree/main/src/time",
        editeur="Serveur de référence MCP",
        popularite=USAGE_COURANT,
    ),
)
