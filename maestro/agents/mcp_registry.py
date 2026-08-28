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
*découverte ≠ installation*. Seule une entrée de l'**allowlist** est
instanciable — `instancier` refuse tout id qui n'y est pas, jamais de
`npx -y <pkg arbitraire>`.

⚠ Cette section décrit le dispositif d'origine, où l'allowlist *était* le
registre : une intégration n'existait pour Maestro que si elle avait été curée
ici, en clair, revue et versionnée. Ce n'est plus tout à fait vrai depuis #677
(la bibliothèque **découvre** au-delà de l'allowlist) ni depuis #678 (un geste
humain peut **faire entrer** une découverte dans l'allowlist) — voir les deux
sections en bas de cet en-tête. Le garde-fou, lui, n'a pas bougé : il a
seulement cessé de porter deux rôles à la fois.

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

## Deux sources, une seule recherche (#677, parent #673)

La bibliothèque a **deux origines** et les rend ensemble (la porte d'admission,
ci-dessous, en tire une troisième *source*) :

- les entrées **curées** (`SEED`) — écrites à la main, relues en revue de code,
  versionnées. Elles *sont* l'allowlist, donc les seules instanciables ;
- les entrées **découvertes** — traduites du miroir du registre MCP officiel
  (`maestro.agents.mcp_amont`, lot 1 ; `maestro.agents.mcp_traduction`, lot 2),
  visibles et cherchables, **jamais montables**.

⚠ **La composition ne touche pas au garde-fou, et c'est tout son dessin.** Les
deux sources vivent dans **deux index séparés** : `instancier` et `get` ne
regardent que l'index curé — ils n'ont pas eu une ligne à changer, et une entrée
découverte est non instanciable *par construction* plutôt que par un test qu'on
aurait pu oublier d'écrire. Ce qui compose est la **lecture** (`lister`,
`rechercher`, `trouver`, `tags`), jamais l'instanciation.

## La porte d'admission (#678, parent #673)

Une découverte n'est pas condamnée à le rester : un **geste humain tracé** la
promeut dans l'allowlist. C'est l'`Admission`, et c'est ce qui tient la promesse
du parent — *fédérer la découverte sans fédérer l'installation*. Le garde-fou de
[docs/19](../../docs/19-securite-modele-de-menace.md) n'est pas levé, il devient
**exact** : jusqu'ici l'allowlist portait deux rôles (« ce qu'on connaît » et
« ce qu'on autorise »), elle n'en garde qu'un.

Une entrée admise entre donc dans l'**index curé** — elle est montable, c'est
tout le point — et se distingue malgré tout du seed, parce que les deux ne se
relisent pas de la même façon : le seed est du **code** (revue de code), une
admission est une **donnée d'installation** (un geste daté, signé, révocable).
D'où trois sources et non deux :

| `source` | d'où | `curee` | montable |
|---|---|---|---|
| `curee` | `SEED`, écrit à la main | `True` | oui |
| `admise` | l'amont, **plus** un geste humain | `True` | oui |
| `decouverte` | l'amont seul | `False` | **non** |

⚠ `curee` (le booléen) et `source` (les trois valeurs) ne répondent pas à la
même question, et les confondre est le seul piège de cette table : le booléen dit
« **montable ?** » — c'est lui que le garde-fou lit —, la source dit « **d'où ça
vient ?** » — c'est elle que l'écran affiche. Une admise est donc `curee: true`
et `source: "admise"`, sans contradiction. Le filtre `source=curee` rend le seed
**seul**, parce qu'un écran qui montre la provenance a besoin de séparer ce qui a
été relu en revue de code de ce qu'un clic a promu hier.

Trois règles portent l'admission, et chacune est un critère du ticket :

1. **L'entrée admise est FIGÉE** (`Admission.entree`). Ce que la bibliothèque
   sert d'une admise ne vient pas du miroir d'aujourd'hui mais de
   l'enregistrement d'hier : une nouvelle version amont ne change **pas** la
   version admise, elle produit un `SignalAmont` que quelqu'un lira. Sans ce
   figement, l'admission autoriserait une version et en monterait une autre —
   c'est-à-dire exactement le trou que la porte est censée fermer.
2. **Rien ne disparaît en silence** (`SignalAmont`). Une admise dont l'amont
   passe `deprecated`, `deleted`, ou qui sort du miroir, reste servie **avec son
   signal** : la retirer d'office casserait un serveur monté sans le dire, et
   nous n'avons pas à trancher à la place de qui l'a admise.
3. **Une révocation ne s'oublie pas.** L'admission révoquée reste dans le
   journal et le registre la garde de côté (`revocation_de`), pour que
   `instancier` puisse **nommer** ce qui s'est passé plutôt que rendre le
   « hors allowlist » d'un id inconnu.

Le **magasin** (le disque), la **politique** (le point d'extension d'entreprise)
et la **veille** (confronter une admission au miroir courant) vivent dans
`maestro.agents.mcp_admission` — ici il n'y a que les structures de données.

Trois règles portent la composition :

1. **Curées d'abord** (`_rang`). Le palier d'usage (`USAGE_*`) reste le rang des
   curées ; une découverte n'a **aucun palier à inventer**, donc la source est
   la clé *primaire* du tri et non un effet de bord d'un `popularite` à zéro —
   sans quoi une curée à palier nul se retrouverait mêlée aux découvertes.
2. **Le seed gagne toute collision.** Un id d'amont qui heurte `ID_RESERVES`, un
   id du seed ou une entrée déjà admise est **écarté** : c'est l'index curé qui
   est instanciable, et le masquer par une découverte le rendrait injoignable.
3. **Une découverte fautive ne fait pas tomber la bibliothèque.** Le seed est du
   code : une entrée invalide y est un bug, et la construction lève. Le miroir
   est de la **donnée d'amont**, à des dizaines de milliers d'entrées : une
   entrée qui ne se valide pas est comptée et écartée, jamais propagée en
   exception. Les deux moitiés de cette asymétrie sont voulues.

Le **câblage** (lire le miroir, le traduire, en dater la provenance) ne vit pas
ici mais dans `maestro.agents.mcp_federation` : ce module reste une structure de
données, sans rien savoir ni du réseau ni du disque.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
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


#: Les trois sources de la bibliothèque (#677, #678). `curee` est l'allowlist
#: écrite à la main ; `decouverte` est ce que le miroir du registre officiel a
#: rapporté, visible et cherchable, jamais montable ; `admise` est une
#: découverte qu'un **geste humain tracé** a promue dans l'allowlist — montable
#: comme une curée, mais relue autrement (voir l'en-tête du module).
SOURCE_CUREE = "curee"
SOURCE_DECOUVERTE = "decouverte"
SOURCE_ADMISE = "admise"

#: Les valeurs qu'un filtre de source accepte. `toutes` est le **défaut** et il
#: est nommé plutôt que sous-entendu par une absence : une route qui reçoit
#: `source=` vide doit servir toutes les sources, pas se demander laquelle.
SOURCE_TOUTES = "toutes"
SOURCES: tuple[str, ...] = (SOURCE_TOUTES, SOURCE_CUREE, SOURCE_DECOUVERTE, SOURCE_ADMISE)

#: Ce que l'amont dit **aujourd'hui** d'une entrée admise **hier** (#678). Quatre
#: genres, et aucun ne retire quoi que ce soit : ils signalent.
#:
#: - `amont_depreciee` : l'amont a passé l'entrée `deprecated` ;
#: - `amont_supprimee` : l'amont l'a passée `deleted` (modération) et le miroir
#:   la porte encore — défensif, `MiroirAmont` les retire normalement ;
#: - `amont_disparue` : elle n'est plus dans le miroir. Les deux causes se
#:   confondent à la lecture — retirée par la modération, ou plus servie — donc
#:   le message les nomme toutes deux plutôt que d'en choisir une ;
#: - `version_nouvelle` : l'amont publie une autre version que la version
#:   admise. **Rien ne bouge** : promouvoir la nouvelle demande un nouveau geste.
SIGNAL_DEPRECIEE = "amont_depreciee"
SIGNAL_SUPPRIMEE = "amont_supprimee"
SIGNAL_DISPARUE = "amont_disparue"
SIGNAL_VERSION = "version_nouvelle"
SIGNAUX: tuple[str, ...] = (
    SIGNAL_DEPRECIEE,
    SIGNAL_SUPPRIMEE,
    SIGNAL_DISPARUE,
    SIGNAL_VERSION,
)


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
class ProvenanceDecouverte:
    """D'où viennent les entrées **découvertes**, et de quand elles datent (#677).

    Le pendant de `Provenance` pour la seconde source, et il ne répond pas à la
    même question : une liste curée se date par sa **revue humaine**, un miroir
    par son **dernier rafraîchissement** et le **nombre** d'entrées qu'il porte.
    Confondre les deux dans un seul objet obligerait chacun à porter les champs
    vides de l'autre, et l'écran à deviner lesquels sont significatifs.

    Les champs recopient ceux d'`EtatMiroir` (`maestro.agents.mcp_amont`) plutôt
    que d'importer le miroir : ce module ne connaît ni le disque ni le réseau, et
    c'est `mcp_federation` qui fait la conversion — un seul endroit à corriger si
    l'état du miroir gagne un champ.

    ⚠ `retenues` n'est pas `nombre`. Le miroir compte ce qu'il a rapporté, la
    bibliothèque ce qu'elle a **su traduire et garder** : l'écart (entrées non
    traduisibles, collisions avec le seed) est une information à montrer, pas un
    trou à masquer en n'exposant qu'un seul chiffre.
    """

    #: Le registre moissonné, ou "" quand aucun miroir n'est branché.
    amont: str = ""
    #: Horodatage ISO du dernier rafraîchissement réussi du miroir.
    rafraichi_le: str = ""
    #: Horodatage ISO du dernier moissonnage **complet**.
    moissonne_le: str = ""
    #: Le nombre d'entrées que le miroir porte.
    nombre: int = 0
    #: Le nombre d'entrées effectivement servies par la bibliothèque.
    retenues: int = 0
    #: La dernière cause d'échec du miroir, vide quand tout va bien.
    cause: str = ""
    #: Horodatage ISO de ce dernier échec.
    echoue_le: str = ""

    @property
    def moissonnee(self) -> bool:
        """Un miroir a-t-il déjà rapporté quelque chose ?

        La question que la `PROVENANCE` curée résout par une phrase figée
        (« jamais moissonnée ») et que le critère 5 du parent demande de rendre
        vraie. Un miroir branché mais vide répond **non** : ce qui compte est
        qu'une entrée en soit sortie, pas qu'une URL soit configurée.
        """
        return bool(self.rafraichi_le and self.nombre)

    def to_dict(self) -> dict[str, Any]:
        """Réémet la provenance de la découverte en dict JSON-sérialisable."""
        return {
            "source": SOURCE_DECOUVERTE,
            "amont": self.amont,
            "rafraichi_le": self.rafraichi_le,
            "moissonne_le": self.moissonne_le,
            "nombre": self.nombre,
            "retenues": self.retenues,
            "moissonnee": self.moissonnee,
            "cause": self.cause,
            "echoue_le": self.echoue_le,
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
class SignalAmont:
    """Un écart entre ce qui a été **admis** et ce que l'amont dit aujourd'hui (#678).

    Il est **calculé**, jamais persisté : le journal des admissions enregistre un
    geste humain, pas l'état d'un miroir qui bouge toutes les heures. C'est
    `maestro.agents.mcp_admission.veiller` qui les produit, à chaque fédération,
    en confrontant les admissions au miroir courant.

    `genre` est un code stable (voir `SIGNAUX`) sur lequel une UI groupe ou
    filtre ; `message` est la phrase qu'on montre. Les deux, jamais l'un sans
    l'autre — c'est la règle des refus de la traduction (#676), pour la même
    raison : un code seul n'apprend rien à qui lit, une phrase seule ne se compte
    pas.
    """

    id: str
    genre: str
    message: str
    #: La version que l'amont sert aujourd'hui (vide si l'entrée a disparu).
    version_amont: str = ""
    #: Le statut amont observé (`active`/`deprecated`/`deleted`), vide si disparue.
    statut_amont: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Réémet le signal en dict JSON-sérialisable (forme publique API/UI)."""
        return {
            "id": self.id,
            "genre": self.genre,
            "message": self.message,
            "version_amont": self.version_amont,
            "statut_amont": self.statut_amont,
        }


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

    `curee`, `version`, `depot` et `statut` datent de la fédération (#677), et
    `publie_le` de l'écran qui les montre (#679). Le premier dit **de quelle
    source** l'entrée vient ; il vaut `True` par défaut, si bien que le seed et
    toute entrée écrite à la main restent curées sans rien déclarer — la valeur
    qu'il portait déjà en dur dans `to_dict()`. Les quatre autres sont les
    signaux que **seul l'amont** fournit (la version épinglée, le dépôt, le
    statut `active`/`deprecated`, la date de publication) et restent vides sur
    une entrée curée, dont ils ne diraient rien : le seed n'épingle pas de
    version, c'est la revue de code qui le date.

    ⚠ `publie_le` est un horodatage **d'amont**, pas une date de revue : il dit
    quand l'éditeur a publié *cette version-là*, ce qui est le seul des quatre
    signaux à répondre à « depuis quand ça existe ? ». Une entrée admise le
    garde figé avec le reste (#678) — l'âge de ce qu'on a admis ne bouge pas
    quand l'amont republie.

    `admission` et `signaux` datent de la porte d'admission (#678) et sont posés
    par `RegistreMcp`, jamais par l'appelant — même règle que `curee`, et pour la
    même raison : une entrée qui se déclarerait admise elle-même serait une
    entrée montable sans geste humain derrière. Le premier porte la traçabilité
    (qui, quand, quelle version, quelle source) ; le second, ce que l'amont dit
    d'elle **depuis** (dépréciation, disparition, version plus récente).
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
    curee: bool = True
    version: str = ""
    depot: str = ""
    statut: str = ""
    publie_le: str = ""
    admission: Admission | None = None
    signaux: tuple[SignalAmont, ...] = ()

    @property
    def source(self) -> str:
        """La provenance de l'entrée : `curee`, `admise` ou `decouverte` (#678).

        Dérivée, jamais déclarée : `admission` et `curee` sont posés par
        `RegistreMcp` selon l'argument qui a porté l'entrée, si bien qu'il n'y a
        pas deux vérités à tenir d'accord. Une admise est `curee=True` **et**
        `source="admise"` — le booléen répond à « montable ? », la source à
        « d'où ça vient ? » (voir l'en-tête du module).
        """
        if self.admission is not None:
            return SOURCE_ADMISE
        return SOURCE_CUREE if self.curee else SOURCE_DECOUVERTE

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
        UI affiche pour guider la saisie.

        `curee` marque l'appartenance à l'allowlist, et depuis #677 il **dit la
        vérité** au lieu de valoir `true` en dur : la bibliothèque a plusieurs
        sources, toutes ne sont pas instanciables. `source` ne le redit pas — il
        dit **autre chose** depuis #678, et c'est le seul point de lecture
        délicat de cette forme : une entrée `admise` est montable (`curee: true`)
        tout en venant de l'amont (`source: "admise"`). Le booléen répond à
        « montable ? », la source à « d'où ça vient ? ».

        `admission` porte la trace du geste (qui, quand, quelle version, quelle
        source amont) — **sans** l'entrée qu'il a figée, qui est celle-ci : la
        réémettre ferait boucler la forme sur elle-même. `signaux` porte ce que
        l'amont dit d'elle depuis.
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
            "curee": self.curee,
            "source": self.source,
            "version": self.version,
            "depot": self.depot,
            "statut": self.statut,
            "publie_le": self.publie_le,
            "admission": self.admission.trace() if self.admission is not None else None,
            "signaux": [s.to_dict() for s in self.signaux],
        }


@dataclass(frozen=True)
class Admission:
    """Le **geste humain tracé** qui fait entrer une découverte dans l'allowlist (#678).

    C'est la porte du parent #673 : le registre officiel dit « ce serveur
    existe », jamais « ce serveur est sûr » — la seconde question reste la nôtre,
    et cet objet est la réponse écrite. Il porte trois choses que rien d'autre ne
    tient ensemble :

    1. **l'entrée figée** (`entree`) — ce qui a été autorisé, au caractère près.
       C'est elle que la bibliothèque sert, jamais la traduction du miroir
       d'aujourd'hui : sinon une nouvelle version amont changerait ce qu'on monte
       sans que personne l'ait admis, ce que le critère 3 du ticket interdit ;
    2. **sa source** (`nom_amont`, `version`, `editeur`, `depot`, `amont`,
       `miroir_le`) — d'où elle vient et de quand elle date. Un identifiant de
       paquet lu dans un enregistrement d'éditeur au namespace vérifié n'est pas
       de la mémoire, et c'est ce qui lève la règle de curation de #271 (« ne
       jamais écrire un `npx -y <paquet>` de mémoire ») sans l'affaiblir ;
    3. **le geste** (`par`, `le`, `note`) et sa **révocation** (`revoquee_par`,
       `revoquee_le`, `motif`), qui ne s'efface pas : une admission révoquée
       reste dans le journal, pour que le refus d'instanciation puisse dire ce
       qui s'est passé au lieu de rendre le « hors allowlist » d'un id inconnu.

    Immuable, comme tout ce module : révoquer produit une **nouvelle**
    `Admission` (`replace`), et ré-admettre en produit une autre encore.
    """

    id: str
    entree: EntreeRegistre
    nom_amont: str = ""
    version: str = ""
    editeur: str = ""
    depot: str = ""
    #: Le registre moissonné dont l'entrée vient (`MiroirAmont.amont`).
    amont: str = ""
    #: L'horodatage du miroir au moment de l'admission (`EtatMiroir.rafraichi_le`) —
    #: de quand datait la matière qu'on a admise, jamais quand on l'a admise.
    miroir_le: str = ""
    par: str = ""
    le: str = ""
    note: str = ""
    revoquee_par: str = ""
    revoquee_le: str = ""
    motif: str = ""

    @property
    def active(self) -> bool:
        """L'admission vaut-elle encore autorisation de monter ?

        La révocation se lit sur `revoquee_le` et non sur un booléen à côté :
        deux champs pour un seul fait finiraient par se contredire, et c'est
        la panne que #365 a supprimée ailleurs dans ce dépôt.
        """
        return not self.revoquee_le

    def trace(self) -> dict[str, Any]:
        """La traçabilité **sans** l'entrée — ce qu'`EntreeRegistre.to_dict` emboîte.

        Deux formes plutôt qu'une, et la raison est structurelle : l'entrée porte
        son admission, l'admission porte son entrée. Réémettre les deux l'une
        dans l'autre ferait boucler la forme publique. `trace()` est donc la vue
        depuis l'entrée, `to_dict()` la vue depuis le journal.
        """
        return {
            "id": self.id,
            "nom_amont": self.nom_amont,
            "version": self.version,
            "editeur": self.editeur,
            "depot": self.depot,
            "amont": self.amont,
            "miroir_le": self.miroir_le,
            "par": self.par,
            "le": self.le,
            "note": self.note,
            "active": self.active,
            "revoquee_par": self.revoquee_par,
            "revoquee_le": self.revoquee_le,
            "motif": self.motif,
        }

    def to_dict(self) -> dict[str, Any]:
        """La forme du **journal** (et du disque) : la trace, plus l'entrée figée."""
        return {**self.trace(), "entree": self.entree.to_dict()}

    @classmethod
    def from_dict(cls, brut: Mapping[str, Any]) -> Admission:
        """Relit une admission écrite par `to_dict` — **strict sur ce qui autorise**.

        L'id et l'entrée figée sont ce qui fait entrer un serveur dans
        l'allowlist : une ligne qui n'en porte pas n'est pas une admission
        incomplète, c'est une autorisation qu'on ne sait pas lire, et on lève
        (`ValueError`). Le reste est de la traçabilité : un champ absent vaut
        vide, ce qui laisse relisible un journal écrit par une version
        antérieure. L'`admission` et les `signaux` de l'entrée figée sont
        **ignorés** à la relecture : le registre les repose, eux seuls savent
        depuis quel argument l'entrée est arrivée.
        """
        id_ = _texte(brut.get("id"))
        entree = brut.get("entree")
        if not id_ or not isinstance(entree, Mapping):
            raise ValueError(
                f"admission MCP invalide : « id » et « entree » sont requis (lu : {id_!r})."
            )
        return cls(
            id=id_,
            entree=entree_depuis_dict(entree),
            nom_amont=_texte(brut.get("nom_amont")),
            version=_texte(brut.get("version")),
            editeur=_texte(brut.get("editeur")),
            depot=_texte(brut.get("depot")),
            amont=_texte(brut.get("amont")),
            miroir_le=_texte(brut.get("miroir_le")),
            par=_texte(brut.get("par")),
            le=_texte(brut.get("le")),
            note=_texte(brut.get("note")),
            revoquee_par=_texte(brut.get("revoquee_par")),
            revoquee_le=_texte(brut.get("revoquee_le")),
            motif=_texte(brut.get("motif")),
        )


class RegistreMcp:
    """La bibliothèque : trois sources, une recherche, **un seul garde-fou**.

    `rechercher` filtre par nom/tag (recherche libre, insensible à la casse et
    aux accents) ; `trouver`/`lister` exposent les entrées de toutes les
    sources ; `get`/`instancier` ne regardent que l'**allowlist** — le seed et
    ce qu'une admission y a fait entrer —, et `instancier` reste la seule voie
    template → liaison.

    Construit par défaut sur le seed en code (`RegistreMcp.curee()`) ; la
    fédération (`maestro.agents.mcp_federation`), les tests (#134) et une V1 en
    base peuvent en injecter un autre — le contrat ne change pas.

    ⚠ **La source d'une entrée est décidée par l'argument qui la porte**, jamais
    par le drapeau qu'elle porte : tout ce qui arrive par `decouvertes` est
    marqué `curee=False` ici même, et tout ce qui arrive par `admissions` reçoit
    son `admission` ici aussi. Se fier au drapeau de l'appelant laisserait une
    entrée d'amont oubliée à `curee=True` — c'est-à-dire une entrée présentée
    comme curée sans être dans l'allowlist, exactement le mensonge que le
    garde-fou ne doit jamais dire.

    ⚠ **Les admissions arrivent TOUTES par le même argument**, actives et
    révoquées, et c'est le registre qui trie : le magasin est le journal, il
    n'a pas à décider ce qui autorise. Une révoquée n'entre nulle part dans les
    listes — elle est gardée de côté pour que `instancier` puisse **nommer** la
    révocation au lieu de rendre le refus d'un id inconnu.
    """

    def __init__(
        self,
        entrees: Iterable[EntreeRegistre],
        provenance: Provenance | None = None,
        *,
        decouvertes: Iterable[EntreeRegistre] = (),
        admissions: Iterable[Admission] = (),
        signaux: Iterable[SignalAmont] = (),
        provenance_decouverte: ProvenanceDecouverte | None = None,
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
            index[entree.id] = replace(entree, curee=True, admission=None)
        self._entrees = index
        self._signaux = tuple(signaux)
        self._signaux_par_id: dict[str, tuple[SignalAmont, ...]] = {}
        for signal in self._signaux:
            self._signaux_par_id[signal.id] = (
                *self._signaux_par_id.get(signal.id, ()),
                signal,
            )
        self._admissions, self._revoquees, self._admissions_ecartees = self._indexe_admissions(
            admissions
        )
        self._decouvertes, ecartees = self._indexe_decouvertes(decouvertes)
        self._ecartees = (*self._admissions_ecartees, *ecartees)
        self.provenance = provenance or PROVENANCE
        self.provenance_decouverte = replace(
            provenance_decouverte or ProvenanceDecouverte(),
            retenues=len(self._decouvertes),
        )
        # La botte de foin et l'ordre sont calculés **une fois**. `rechercher`
        # est O(n) par requête et `_foin` fait une décomposition NFKD par
        # entrée : à 29 entrées curées personne ne l'a jamais vu, à des dizaines
        # de milliers d'entrées d'amont c'est le coût de chaque frappe au
        # clavier de l'écran. Le registre étant immuable, il n'y a rien à
        # invalider — la mémoire tenue est celle d'un index, pas d'un cache.
        self._toutes = tuple(
            sorted((*self._entrees.values(), *self._decouvertes.values()), key=_rang)
        )
        self._foins = {e.id: _foin(e) for e in self._toutes}

    def _indexe_admissions(
        self, admissions: Iterable[Admission]
    ) -> tuple[dict[str, Admission], dict[str, Admission], tuple[str, ...]]:
        """Range les admissions, et **fait entrer les actives dans l'allowlist**.

        Rend `(actives, révoquées, écartées)` et **complète `self._entrees`** au
        passage : c'est le seul endroit où une entrée d'amont devient montable,
        et le mettre ailleurs reviendrait à avoir deux portes d'admission.

        Comme les découvertes, une admission fautive est **écartée et comptée**,
        jamais propagée en exception : le journal est de la donnée d'installation
        que la Control Tower écrit à chaud, pas du code relu en revue. Mais
        l'asymétrie du lot 3 se resserre d'un cran ici, parce que ce qui est en
        jeu n'est plus l'affichage mais le **montage** : une admission dont
        l'id heurte le seed est écartée (le seed gagne, règle 2 du module), une
        admission dont le gabarit ne se monterait pas l'est aussi — mieux vaut
        une entrée absente de la bibliothèque qu'une entrée montable dont
        personne n'a validé le gabarit.
        """
        actives: dict[str, Admission] = {}
        revoquees: dict[str, Admission] = {}
        ecartees: list[str] = []
        for admission in admissions:
            if not admission.active:
                # Une révocation ne s'écarte pas : elle se garde de côté, c'est
                # ce qui permet à `instancier` de nommer ce qui s'est passé.
                revoquees[admission.id] = admission
                continue
            if admission.id in ID_RESERVES:
                ecartees.append(f"{admission.id} (admission — id réservé par une route)")
                continue
            if admission.id in self._entrees:
                ecartees.append(f"{admission.id} (admission — déjà curé, le seed gagne)")
                continue
            if admission.id in actives:
                ecartees.append(f"{admission.id} (admission — doublon dans le journal)")
                continue
            entree = admission.entree
            if entree.mode_auth not in MODES_AUTH:
                ecartees.append(f"{admission.id} (admission — mode d'auth {entree.mode_auth!r})")
                continue
            try:
                entree.vers_serveur()
            except ValueError as exc:
                ecartees.append(f"{admission.id} (admission — {exc})")
                continue
            actives[admission.id] = admission
            self._entrees[admission.id] = replace(
                entree,
                id=admission.id,
                curee=True,
                admission=admission,
                signaux=self._signaux_par_id.get(admission.id, ()),
            )
        return actives, revoquees, tuple(ecartees)

    def _indexe_decouvertes(
        self, decouvertes: Iterable[EntreeRegistre]
    ) -> tuple[dict[str, EntreeRegistre], tuple[str, ...]]:
        """Indexe les entrées découvertes — **sans jamais lever** (règle 3 du module).

        Rend l'index et les ids écartés avec leur cause. Quatre motifs d'écart,
        et l'ordre est celui de la gravité décroissante : un id **réservé** par
        une route, une **collision avec l'allowlist** (le curé gagne : c'est lui
        qui est instanciable, le masquer le rendrait injoignable), un **doublon**
        d'amont, et un gabarit qui **ne se monterait pas**. Ce dernier ne devrait
        pas arriver — la traduction valide déjà —, et c'est précisément pourquoi
        on le rattrape ici plutôt que de parier dessus : la bibliothèque entière
        tomberait sur une seule ligne fautive d'un fichier qu'on ne relit pas.

        ⚠ Une entrée **admise** est dans l'allowlist, donc sa jumelle d'amont
        tombe sous la collision — et c'est voulu : les deux sont la *même*
        entrée, celle qui est servie est la version **figée** de l'admission et
        non la traduction du miroir d'aujourd'hui (#678, règle 1). La cause le
        dit avec un autre mot que « le seed gagne », faute de quoi on chercherait
        au seed une entrée qui n'y est pas.
        """
        index: dict[str, EntreeRegistre] = {}
        ecartees: list[str] = []
        for entree in decouvertes:
            if entree.id in ID_RESERVES:
                ecartees.append(f"{entree.id} (id réservé par une route)")
                continue
            if entree.id in self._admissions:
                ecartees.append(f"{entree.id} (déjà admise — la version admise fait foi)")
                continue
            if entree.id in self._entrees:
                ecartees.append(f"{entree.id} (déjà curé — le seed gagne)")
                continue
            if entree.id in index:
                ecartees.append(f"{entree.id} (doublon d'amont)")
                continue
            if entree.mode_auth not in MODES_AUTH:
                ecartees.append(f"{entree.id} (mode d'auth {entree.mode_auth!r})")
                continue
            try:
                entree.vers_serveur()
            except ValueError as exc:
                ecartees.append(f"{entree.id} ({exc})")
                continue
            index[entree.id] = replace(entree, curee=False)
        return index, tuple(ecartees)

    @property
    def decouvertes_ecartees(self) -> tuple[str, ...]:
        """Les entrées d'amont écartées à la construction, avec leur cause.

        Nommées et non tues : une bibliothèque qui sert 24 998 entrées sur 25 000
        doit pouvoir dire lesquelles manquent, sinon l'écart entre le compte du
        miroir et le sien est un mystère plutôt qu'une information. Les
        admissions écartées y figurent aussi, préfixées `admission —` : ce sont
        les plus graves de toutes, puisqu'un geste humain les avait autorisées.
        """
        return self._ecartees

    @property
    def signaux(self) -> tuple[SignalAmont, ...]:
        """Tout ce que l'amont dit des entrées admises **depuis** leur admission (#678)."""
        return self._signaux

    def admissions(self, *, revoquees: bool = False) -> tuple[Admission, ...]:
        """Le journal des admissions **actives**, ou les révoquées (`revoquees=True`).

        Deux listes et non une avec un drapeau à filtrer : les appelants sont
        deux — l'écran des admissions veut les actives, l'audit d'une révocation
        veut les autres —, et un seul verbe rendant tout obligerait chacun à
        redire la règle de tri.
        """
        journal = self._revoquees if revoquees else self._admissions
        return tuple(sorted(journal.values(), key=lambda a: (a.le, a.id)))

    def admission_de(self, id: str) -> Admission | None:
        """L'admission **active** qui a fait entrer `id` dans l'allowlist, ou None."""
        return self._admissions.get(id)

    def revocation_de(self, id: str) -> Admission | None:
        """L'admission **révoquée** de `id`, ou None — ce qui nomme un refus (#678).

        Gardée de côté et jamais listée : elle n'autorise plus rien, mais sans
        elle un serveur retiré de l'allowlist se refuserait avec les mots d'un id
        inconnu, et le pool d'un projet ne saurait pas dire *pourquoi* une
        intégration montée hier n'est plus curée aujourd'hui.
        """
        return self._revoquees.get(id)

    def signaux_de(self, id: str) -> tuple[SignalAmont, ...]:
        """Ce que l'amont dit de l'entrée `id` depuis son admission — () si rien."""
        return self._signaux_par_id.get(id, ())

    @classmethod
    def curee(cls) -> RegistreMcp:
        """Le registre curé : le seed en code (`SEED`) et la provenance qui le date.

        Une seule source, donc le comportement d'avant #677 **au bit près** :
        c'est ce que les tests et les appelants qui n'ont pas de miroir attendent.
        """
        return cls(SEED, PROVENANCE)

    def lister(self, source: str = SOURCE_TOUTES) -> tuple[EntreeRegistre, ...]:
        """Les entrées de `source`, **curées d'abord puis les plus courantes** (`_rang`).

        `source` vaut `toutes` (défaut), `curee`, `admise` ou `decouverte` — une
        valeur inconnue lève plutôt que de servir silencieusement autre chose que
        ce qu'on lui demande.

        ⚠ `curee` rend le **seed seul** depuis #678, pas tout ce qui est
        montable : les admises ont leur propre valeur. C'est le filtre d'un écran
        qui montre la provenance, et il n'y a pas d'ambiguïté à lever — ce qui
        répond à « montable ? » est le champ `curee` d'une entrée, jamais le
        filtre qui l'a servie.
        """
        if source == SOURCE_TOUTES:
            return self._toutes
        if source in SOURCES:
            return tuple(e for e in self._toutes if e.source == source)
        raise ValueError(
            f"source de registre MCP inconnue : {source!r} (attendu : {', '.join(SOURCES)})."
        )

    def get(self, id: str) -> EntreeRegistre | None:
        """L'entrée de l'**allowlist** d'id `id`, ou None si elle n'y est pas.

        ⚠ Volontairement aveugle aux découvertes, et c'est le garde-fou : ses
        appelants (`POST /api/mcp/pool`, l'enrichissement du pool) demandent « ce
        serveur est-il montable ? » et non « existe-t-il ? ». Pour la seconde
        question, `trouver`.

        L'allowlist contient le seed **et** les entrées admises (#678) : c'est la
        définition même de l'admission, et c'est pourquoi cette méthode n'a pas
        eu une ligne à changer — les admises entrent dans le même index, en
        amont d'elle.
        """
        return self._entrees.get(id)

    def trouver(self, id: str) -> EntreeRegistre | None:
        """L'entrée d'id `id` **quelle que soit sa source**, curée d'abord, ou None.

        Ce que sert `GET /api/mcp/registre/{id}` : la fiche d'une entrée que
        l'écran vient de lister. Elle porte `curee` — donc le lecteur sait ce
        qu'il regarde — et n'ouvre aucune voie de montage, `instancier` ne
        passant pas par ici.
        """
        return self._entrees.get(id) or self._decouvertes.get(id)

    def rechercher(
        self, requete: str = "", source: str = SOURCE_TOUTES
    ) -> tuple[EntreeRegistre, ...]:
        """Les entrées dont le nom, l'éditeur, un tag (ou l'id/la description) porte `requete`.

        Recherche libre, insensible à la casse et aux accents ; une requête vide
        rend tout le registre. Le résultat est trié **curées d'abord, puis les
        plus courantes** (#271, #677) : sur une bibliothèque de plusieurs
        dizaines de milliers d'entrées, l'ordre de déclaration ne veut plus rien
        dire pour qui cherche « base de données ». Id et description restent dans
        la botte de foin bien que le critère ne nomme que nom/éditeur/tags :
        c'est un sur-ensemble, et le retirer ferait échouer des recherches qui
        marchent (« tickets » vit dans les tags, mais « merge request » vit dans
        une description).

        ⚠ C'est **notre** recherche qui joue sur toutes les sources, et c'est la
        décision du parent (#673) : celle de l'amont est une sous-chaîne sur le
        seul nom (`feature flag` y rend zéro résultat sur 25 000 entrées). On
        moissonne chez lui, on cherche chez nous.
        """
        besoin = _normalise(requete)
        candidates = self.lister(source)
        if not besoin:
            return candidates
        return tuple(e for e in candidates if besoin in self._foins[e.id])

    def tags(self) -> tuple[str, ...]:
        """Tous les tags du registre, dédoublonnés et triés — les pistes de recherche.

        Ce que l'UI propose quand une recherche ne rend rien (#271) : un
        cul-de-sac se sort en montrant *par quoi* on peut chercher, jamais en
        répétant que la requête est vide de résultats.

        Toutes les sources y contribuent, ce qui ne change rien aujourd'hui :
        l'amont ne déclare aucun tag et la traduction n'en fabrique pas (#676).
        Composer quand même évite qu'un jour où il en déclarerait, la moitié des
        pistes reste invisible faute d'avoir pensé à toucher cette méthode.
        """
        return tuple(sorted({tag for e in self._toutes for tag in e.tags}))

    def instancier(self, id: str, *, nom: str | None = None) -> ServeurMcp:
        """Instancie l'entrée `id` de l'allowlist en `ServeurMcp` — **garde-fou supply-chain**.

        Seule une entrée de l'allowlist est instanciable (docs/19, découverte ≠
        installation) : un `id` qui n'y est pas lève `ValueError` sans rien
        monter. C'est l'unique voie template → liaison ; le montage effectif
        (résolution des `${VAR}`) reste le rôle de `maestro.agents.mcp.resolus`.

        ⚠ **Le refus nomme le geste qui manque** (#678, critère 1). Trois causes
        et non une, parce qu'elles n'appellent pas le même geste : une entrée
        **découverte** attend une admission, une entrée **révoquée** attend une
        décision qu'on a déjà prise une fois contre elle, un id **inconnu**
        n'attend rien. Le « hors allowlist » unique d'avant les confondait, et
        c'est ce qui rendait la découverte un cul-de-sac : rien à l'écran ne
        disait qu'il existait une porte, ni où elle était.
        """
        entree = self._entrees.get(id)
        if entree is None:
            raise ValueError(self.cause_non_instanciable(id))
        return entree.vers_serveur(nom=nom)

    def cause_non_instanciable(self, id: str) -> str:
        """Pourquoi `id` n'est pas montable — **la phrase, à un seul endroit**.

        Publique parce qu'elle a deux appelants et qu'ils doivent dire la même
        chose : `instancier`, qui lève, et `POST /api/mcp/pool`, qui refuse en
        404 **avant** d'instancier. Recopier la phrase dans la route la ferait
        diverger au premier ajustement — et c'est justement la phrase qui porte
        le critère 1 du ticket, celle qui nomme le geste manquant.

        ⚠ **La révocation est cherchée en premier, et l'ordre est le contenu de
        la décision.** Une entrée révoquée redevient une découverte — plus rien
        ne l'écarte de l'index d'amont —, donc les deux causes sont vraies à la
        fois et la plus informative doit gagner : « personne ne l'a admise » est
        exact et trompeur sur une entrée qu'on a admise puis retirée, où la
        question n'est pas « comment l'admettre ? » mais « pourquoi l'a-t-on
        sortie ? ». L'ordre inverse était la première version de ce code ; le
        banc de vérification l'a prise en défaut.
        """
        revoquee = self._revoquees.get(id)
        if revoquee is not None:
            quand = revoquee.revoquee_le or "?"
            qui = revoquee.revoquee_par or "?"
            motif = f" — {revoquee.motif}" if revoquee.motif else ""
            return (
                f"serveur MCP {id!r} : son admission a été **révoquée** le {quand} "
                f"par {qui}{motif}. Il n'est plus dans l'allowlist et n'est donc "
                "plus instanciable ; le ré-admettre (POST /api/mcp/admissions) est "
                "un nouveau geste, à poser en connaissance de la révocation."
            )
        decouverte = self._decouvertes.get(id)
        if decouverte is not None:
            version = f" (version {decouverte.version})" if decouverte.version else ""
            return (
                f"serveur MCP {id!r} découvert{version} mais **non admis** : non "
                "instanciable. Une entrée du registre officiel n'est pas dans "
                "l'allowlist tant qu'un humain ne l'y a pas fait entrer — c'est "
                "l'admission (POST /api/mcp/admissions), un geste tracé qui fige "
                "la version et enregistre qui l'a admise. Découverte ≠ "
                "installation : voir docs/19."
            )
        return (
            f"serveur MCP {id!r} hors allowlist : non instanciable (découverte ≠ "
            "installation — un serveur doit être curé dans le registre, ou admis "
            "depuis le registre officiel, avant d'être monté ; voir docs/19)."
        )


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


#: Le rang de chaque source dans le tri (`_rang`) — **montables d'abord**, et
#: parmi elles le seed avant les admises : ce qui a été relu en revue de code
#: passe avant ce qu'un clic a promu hier. Un ordre et non un `bool`, parce
#: qu'ils sont trois depuis #678.
_RANG_SOURCE: dict[str, int] = {
    SOURCE_CUREE: 0,
    SOURCE_ADMISE: 1,
    SOURCE_DECOUVERTE: 2,
}


def _rang(entree: EntreeRegistre) -> tuple[int, int, str]:
    """La clé de tri : **source**, puis palier d'usage décroissant, puis nom.

    La source passe en tête depuis #677, et c'est une décision et non un détail :
    le palier `USAGE_*` est le rang **des curées**, une découverte n'en ayant
    aucun à inventer. Trier sur le seul `popularite` rangerait donc les
    découvertes (palier `0`) parmi les curées de palier `0` — mélange que le
    critère du ticket exclut. À source et palier égaux, l'ordre reste
    alphabétique : stable, et sans faux gagnant.
    """
    return (
        _RANG_SOURCE.get(entree.source, len(_RANG_SOURCE)),
        -entree.popularite,
        _normalise(entree.nom),
    )


def _secrets(*variables: tuple[str, str, bool]) -> tuple[VariableSecret, ...]:
    """Petit constructeur du seed : `(clé, description, secret)` → `VariableSecret`."""
    return tuple(VariableSecret(cle, description, secret) for cle, description, secret in variables)


def _texte(valeur: object) -> str:
    """`valeur` si c'est une chaîne, sinon la chaîne vide — jamais un `str(dict)`."""
    return valeur if isinstance(valeur, str) else ""


def _table(valeur: object) -> dict[str, str]:
    """Une table `str → str` lue d'un JSON — les valeurs non textuelles sont écartées."""
    if not isinstance(valeur, Mapping):
        return {}
    return {str(cle): v for cle, v in valeur.items() if isinstance(v, str)}


def _mots(valeur: object) -> tuple[str, ...]:
    """Une liste de chaînes lue d'un JSON — les éléments non textuels sont écartés."""
    if not isinstance(valeur, list):
        return ()
    return tuple(v for v in valeur if isinstance(v, str))


def entree_depuis_dict(brut: Mapping[str, Any]) -> EntreeRegistre:
    """Relit une `EntreeRegistre` écrite par `to_dict` — la moitié manquante du couple.

    Le module savait **émettre** une entrée depuis #131 ; il n'avait jamais eu à
    en relire une, le seed étant du code. La porte d'admission (#678) enregistre
    l'entrée traduite sur le disque, donc il faut savoir la reprendre : c'est
    cette fonction, et c'est le seul décodeur du dépôt pour cette forme (la
    recopier ailleurs ferait deux lectures à tenir d'accord).

    **Tolérant sur la forme, strict sur l'identité.** Un champ absent ou d'un
    type inattendu prend son défaut — un journal écrit par une version
    antérieure reste relisible, et le schéma amont bouge (préversion). L'`id`,
    lui, est requis : sans identité, l'entrée n'est ni indexable ni révocable.

    ⚠ `curee`, `admission` et `signaux` ne sont **pas** relus : ils sont posés
    par `RegistreMcp` selon l'argument qui porte l'entrée (voir sa docstring).
    Les relire ferait entrer dans l'allowlist une entrée qui s'y déclarerait
    elle-même — précisément ce que la porte d'admission existe pour empêcher.
    """
    id_ = _texte(brut.get("id")).strip()
    if not id_:
        raise ValueError("entrée de registre MCP invalide : « id » requis.")
    secrets_bruts = brut.get("secrets")
    secrets: list[VariableSecret] = []
    for secret in secrets_bruts if isinstance(secrets_bruts, list) else []:
        if not isinstance(secret, Mapping):
            continue
        cle = _texte(secret.get("cle")).strip()
        if cle:
            secrets.append(
                VariableSecret(
                    cle=cle,
                    description=_texte(secret.get("description")),
                    secret=bool(secret.get("secret", True)),
                )
            )
    brut_palier = brut.get("popularite")
    # `isinstance(True, int)` est vrai en Python : un `true` JSON deviendrait un
    # palier de 1, c'est-à-dire un rang de tri fabriqué à partir d'un booléen.
    palier: int = 0
    if isinstance(brut_palier, int) and not isinstance(brut_palier, bool):
        palier = brut_palier
    return EntreeRegistre(
        id=id_,
        nom=_texte(brut.get("nom")) or id_,
        description=_texte(brut.get("description")),
        mode_auth=_texte(brut.get("mode_auth")),
        transport=_texte(brut.get("transport")),
        commande=_texte(brut.get("commande")),
        args=_mots(brut.get("args")),
        url=_texte(brut.get("url")),
        env=_table(brut.get("env")),
        headers=_table(brut.get("headers")),
        tags=_mots(brut.get("tags")),
        secrets=tuple(secrets),
        procedure_url=_texte(brut.get("procedure_url")),
        optionnel=bool(brut.get("optionnel", False)),
        editeur=_texte(brut.get("editeur")),
        popularite=palier,
        version=_texte(brut.get("version")),
        depot=_texte(brut.get("depot")),
        statut=_texte(brut.get("statut")),
        publie_le=_texte(brut.get("publie_le")),
    )


#: D'où vient cette liste, et quand elle a été revue (#271) — **affiché** au pied
#: de la bibliothèque, servi par `GET /api/mcp/registre/provenance`.
#:
#: ⚠ `revue_le` se met à jour **avec le seed**, dans le même commit : une date
#: qui ne bouge pas quand la liste bouge est pire qu'une date absente, elle
#: atteste une fraîcheur que personne n'a vérifiée.
#:
#: ⚠ Depuis #677 cette provenance ne décrit plus la bibliothèque entière mais sa
#: **source curée** — d'où « cette liste-ci ». La provenance du miroir est rendue
#: par `ProvenanceDecouverte`, à côté et jamais fondue dans celle-ci.
#:
#: ⚠ #679 a retiré **deux** morceaux de la phrase, et pour deux raisons qui ne se
#: valent pas. « Jamais moissonnée » était encore *vrai de cette liste-ci* et
#: #677 l'avait gardé en le bornant ; mais c'est la phrase que l'écran affiche au
#: pied d'une bibliothèque **à deux sources**, dont l'une est moissonnée — une
#: moitié de vérité posée à côté de son contraire se lit comme une contradiction,
#: et borner la portée dans une docstring ne borne rien à l'écran. « C'est elle,
#: et elle seule, qui est instanciable » était devenu **faux** : la porte
#: d'admission (#678) fait entrer des entrées d'amont dans l'allowlist, et ce
#: qu'il fallait dire à la place n'est pas la source mais l'allowlist elle-même.
#: Ce qui reste — écrite à la main, relue en revue de code, versionnée — est ce
#: qui distingue vraiment cette source, et rien de cela n'a bougé.
PROVENANCE = Provenance(
    resume=(
        "Cette liste-ci est une sélection curée à la main parmi les serveurs MCP "
        "les plus utilisés de l'écosystème, d'après les annuaires publics "
        "ci-dessous : chaque entrée y est écrite, relue en revue de code et "
        "versionnée avec le dépôt. Est instanciable ce qui appartient à "
        "l'allowlist — cette liste, plus ce qu'un geste humain y a admis."
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
