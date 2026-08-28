"""Traduire un `server.json` du registre officiel en entrée de bibliothèque (#676, lot 2/6 de #673).

Le registre décrit un serveur en `server.json` ; Maestro le décrit en
`EntreeRegistre` (`maestro.agents.mcp_registry`). Ce module fait le passage —
et il ne rend jamais qu'une de deux choses : une **entrée complète**, ou un
**refus nommé**. Jamais une entrée à moitié déclarée : c'est la règle de
curation de #271 (« un gabarit qu'on ne sait pas écrire **exactement** n'entre
pas »), et elle ne s'assouplit pas parce que la source devient automatique — ce
qui change avec la fédération, c'est d'où vient l'identifiant de paquet, pas le
niveau d'exigence sur ce qu'on écrit.

## Ce qui décide de tout : `resolus` ne substitue que dans `env` et `headers`

`maestro.agents.mcp.resolus` remplace les `${VAR}` des valeurs d'`env` et de
`headers`, et **de rien d'autre** ([docs/21 §3.4](../../docs/21-configuration-mcp.md)).
Deux refus en découlent directement, et ce sont des faits sur le code, pas des
précautions de style :

- une valeur nécessaire qui vit en **argv** (`packageArguments` /
  `runtimeArguments`) n'est pas résoluble — `args` n'est jamais traversé ;
- un gabarit `{placeholder}` dans l'**URL** d'un `remotes[]` ne l'est pas
  davantage — `url` n'est jamais traversé non plus. Le substituer en silence
  enverrait une URL trouée au transport ; le laisser tel quel aussi. On refuse.

Bonne nouvelle mesurée le 2026-08-28 sur les 600 premières entrées
`version=latest` : **82 % portent un `remotes`** (489 `streamable-http`, 18
`sse`) — exactement la forme que docs/21 préfère (« rien à exécuter, l'URL est
vérifiable »). La contrainte n'écarte donc qu'une minorité, et ce module
l'écarte **en la nommant**, comme docs/21 nomme aujourd'hui `filesystem` et
`postgres` au lieu de les taire.

## L'ordre des candidats, et pourquoi il n'est pas arbitraire

Un document peut porter plusieurs `remotes` et plusieurs `packages`. On les
essaie **tous**, `remotes` d'abord (docs/21 §3.4 : rien à exécuter), et la
première forme qui se traduit **entièrement** gagne. Les formes écartées en
chemin ne disparaissent pas : elles laissent leur cause dans
`Traduction.avertissements`, de sorte qu'une entrée servie par son `sse` de
secours dise pourquoi son `streamable-http` ne convenait pas. Si aucune ne
passe, le refus porte le motif de la **première** (la préférée) et récite les
causes de toutes : c'est ce qui rend un refus diagnostiquable sans rouvrir le
document.

## Ce qui est dérivé, ce qui est refusé, ce qui n'est jamais inventé

- **`mode_auth` est dérivé** (critère du ticket) : aucune variable →
  `sans_secret` ; au moins une variable déclarée `isSecret` → `token_statique`.
  `appairage` et `oauth_importe` restent **réservés à la curation à la main** —
  la dérivation ne les produit jamais, et `MODES_CURATION` le dit *par
  construction* (le complément de `MODES_DERIVES` dans `MODES_AUTH`), pour qu'un
  cinquième mode ajouté demain tombe du côté sûr sans qu'on y pense.
- **Le cas intermédiaire tombe sur `sans_secret`** : des variables, mais aucune
  secrète. C'est fidèle à la définition de `MODES_AUTH` — les modes classent
  *comment un secret s'obtient*, question sans objet quand aucune variable n'en
  est un — et l'UI ne perd rien : c'est `secrets` qu'elle lit pour dresser son
  formulaire, et les variables non secrètes y figurent avec `secret=False`.
- **La version est épinglée ou l'entrée est refusée.** Une version doit
  commencer par un chiffre (`1.2.3`, `v0.4.0`, `2026.1.1`) : `latest`, `next`,
  `stable` et leurs cousines sont des étiquettes flottantes, et une étiquette
  flottante détruit l'argument même qui rend la fédération légitime (« un
  identifiant signé par un éditeur vérifié, **avec sa version épinglée** »). La
  règle est un motif et non une liste noire à tenir à jour.
- **Rien n'est inventé.** `nom` est le dernier segment du nom amont, `editeur`
  son namespace — les deux recomposent le nom amont au caractère près. `tags`
  reste vide (l'amont n'en déclare aucun ; en fabriquer serait fabriquer de la
  métadonnée), et `popularite` reste à `0` : une entrée découverte n'a aucun
  usage mesuré, et ce zéro la range naturellement derrière les entrées curées
  dans le tri de `RegistreMcp.lister`.

## Le `$schema` est lu, et aucune version n'est pariée

Les entrées anciennes servent encore `2025-09-29` quand les récentes servent
`2025-12-11`. Le module **lit** le `$schema` de chaque document et le rend dans
`Traduction.schema` ; une version inconnue n'est pas un refus mais un
avertissement, parce que refuser à chaque montée de version ferait tomber la
fédération entière le jour où l'amont bouge. La sécurité vient d'ailleurs :
chaque champ est cherché sous ses **alias** connus (camelCase d'aujourd'hui,
snake_case d'hier), et un champ qu'on ne retrouverait plus produit un refus
**nommé** (« ni remotes ni packages ») — jamais une entrée bancale.

Enfin, l'entrée produite passe `valide_serveur` avant d'être rendue (note
technique du ticket) : ce qui ne se monterait pas est un refus, pas une entrée.

Ce lot ne touche **ni `SEED` ni le contrat public de `EntreeRegistre.to_dict()`** —
la fédération (fusion des deux sources, admission, provenance) arrive au lot 3.

Tests différés → lot 6 du parent (#680).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maestro.agents.mcp_amont import STATUT_SUPPRIME, EntreeAmont
from maestro.agents.mcp_registry import ID_RESERVES, MODES_AUTH, EntreeRegistre, VariableSecret

#: Les modes d'auth que la **dérivation** peut produire (critère du ticket).
MODES_DERIVES: tuple[str, ...] = ("sans_secret", "token_statique")

#: Les modes **réservés à la curation à la main**, que rien n'écrase — définis
#: comme le complément des dérivables dans `MODES_AUTH` plutôt que recopiés : un
#: cinquième mode ajouté à la classification tomberait ainsi du côté sûr tout
#: seul, là où une seconde liste écrite à la main serait à tenir d'accord.
MODES_CURATION: tuple[str, ...] = tuple(m for m in MODES_AUTH if m not in MODES_DERIVES)

#: Les transports distants de l'amont → nos types d'endpoint. Les alias couvrent
#: les formes vues d'un millésime de schéma à l'autre ; ce qui n'y figure pas est
#: refusé en le nommant, jamais deviné.
TRANSPORTS_DISTANTS: dict[str, str] = {
    "streamable-http": "http",
    "streamable_http": "http",
    "streamablehttp": "http",
    "http": "http",
    "sse": "sse",
}

#: Les couples `registryType` → runtime que Maestro sait écrire **exactement**.
#: Deux seulement, et ce sont les deux déjà présents dans le seed curé (`npx`,
#: `uvx`) : ce sont aussi les seuls où les `environmentVariables` arrivent bien
#: au serveur par l'environnement du processus. Un conteneur (`oci`) les
#: attendrait en `-e` sur sa ligne de commande, c'est-à-dire en argv — donc hors
#: de portée de `resolus`, et c'est pour cette raison qu'il est refusé plutôt que
#: par manque d'envie.
REGISTRES_SUPPORTES: dict[str, str] = {"npm": "npx", "pypi": "uvx"}

#: Les runtimes que l'on sait écrire, avec les drapeaux qui précèdent le paquet.
RUNTIMES_SUPPORTES: dict[str, tuple[str, ...]] = {"npx": ("-y",), "uvx": ()}

#: Les millésimes de `server.schema.json` connus au moment d'écrire ce module.
#: Informatif : une version absente de cette table n'est pas refusée, elle est
#: signalée (voir l'en-tête du module).
SCHEMAS_CONNUS: frozenset[str] = frozenset({"2025-09-29", "2025-12-11"})

#: Le préfixe des variables du gabarit. Les noms sont **namespacés par le
#: serveur** : à 25 000 entrées, deux serveurs déclarant chacun un `API_KEY`
#: partageraient sinon le même emplacement de secret — l'humain fournirait la
#: clé de l'un et l'autre la recevrait, en silence.
PREFIXE_VARIABLE = "MCP"

MOTIF_SANS_FORME = "sans_forme"
MOTIF_TRANSPORT = "transport_non_supporte"
MOTIF_REGISTRE = "registre_non_supporte"
MOTIF_ARGV = "variable_en_argv"
MOTIF_URL = "variable_en_url"
MOTIF_VERSION = "version_non_epinglee"
MOTIF_IDENTITE = "identite_inexploitable"
MOTIF_SUPPRIMEE = "entree_supprimee"
MOTIF_VALIDATION = "validation"

#: Tous les motifs de refus, dans l'ordre de lecture — l'UI (lot 4) et les tests
#: (lot 6) s'y adossent plutôt qu'aux chaînes recopiées.
MOTIFS: tuple[str, ...] = (
    MOTIF_SANS_FORME,
    MOTIF_TRANSPORT,
    MOTIF_REGISTRE,
    MOTIF_ARGV,
    MOTIF_URL,
    MOTIF_VERSION,
    MOTIF_IDENTITE,
    MOTIF_SUPPRIMEE,
    MOTIF_VALIDATION,
)

#: Un gabarit `{placeholder}` dans une valeur amont (URL, en-tête, argument).
_GABARIT = re.compile(r"\{([^{}\s]+)\}")

#: Une version **épinglée** commence par un chiffre (`1.2.3`, `v0.4.0`,
#: `2026.1.1`). Un motif plutôt qu'une liste noire d'étiquettes flottantes :
#: `latest`, `next`, `stable`, `canary`… tombent toutes du même côté sans qu'on
#: ait à les énumérer ni à tenir la liste à jour.
_EPINGLEE = re.compile(r"^v?\d")

_HORS_SLUG = re.compile(r"[^a-z0-9_-]+")
_TIRETS = re.compile(r"-{2,}")
_HORS_ENV = re.compile(r"[^A-Za-z0-9_]+")


class _Irreductible(Exception):
    """Une forme candidate ne se traduit pas — **interne**, muée en `Refus`.

    Portée par une exception et non par un retour : la cause naît au fond d'un
    argument ou d'un en-tête, à trois niveaux de la décision, et la remonter à la
    main obligerait chaque helper à porter un canal d'erreur dont il n'a que
    faire.
    """

    def __init__(self, motif: str, cause: str) -> None:
        super().__init__(cause)
        self.motif = motif
        self.cause = cause


@dataclass(frozen=True)
class Refus:
    """Pourquoi une entrée amont n'est pas devenue une entrée de bibliothèque.

    `motif` est un code stable (voir `MOTIFS`) sur lequel une UI peut grouper ou
    filtrer ; `cause` est la phrase qu'on montre, qui **nomme la forme fautive**
    (`remotes[1]`, `packages[0]`) et ce qui manque. Les deux, jamais l'un sans
    l'autre : un code seul n'apprend rien à qui lit, une phrase seule ne se
    compte pas.
    """

    motif: str
    cause: str

    def to_dict(self) -> dict[str, Any]:
        """Réémet le refus en dict JSON-sérialisable (forme publique API/UI)."""
        return {"motif": self.motif, "cause": self.cause}


@dataclass(frozen=True)
class Traduction:
    """Le résultat d'une traduction : une entrée, **ou** un refus — jamais les deux.

    `avertissements` porte ce qui a été écarté sans faire échouer la traduction
    (une forme candidate refusée au profit d'une autre, une variable facultative
    qu'aucun gabarit ne peut exprimer, un `$schema` inconnu). C'est la moitié
    « nommer au lieu de taire » du contrat : une entrée qui passe peut quand même
    avoir laissé quelque chose derrière elle, et elle le dit.
    """

    nom: str = ""
    schema: str = ""
    entree: EntreeRegistre | None = None
    refus: Refus | None = None
    avertissements: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """La traduction a rendu une entrée (donc `refus` est None)."""
        return self.entree is not None

    def resume(self) -> str:
        """Une ligne lisible — ce qu'on journalise et ce qu'un écran peut citer."""
        if self.entree is not None:
            suffixe = f" · {len(self.avertissements)} avertissement(s)"
            return (
                f"{self.nom or '?'} → {self.entree.id} "
                f"({self.entree.transport}, {self.entree.mode_auth})"
                f"{suffixe if self.avertissements else ''}"
            )
        cause = self.refus.cause if self.refus else "cause inconnue"
        return f"{self.nom or '?'} refusée — {cause}"

    def to_dict(self) -> dict[str, Any]:
        """Réémet la traduction en dict JSON-sérialisable (forme publique API/UI)."""
        return {
            "nom": self.nom,
            "schema": self.schema,
            "ok": self.ok,
            "entree": self.entree.to_dict() if self.entree is not None else None,
            "refus": self.refus.to_dict() if self.refus is not None else None,
            "avertissements": list(self.avertissements),
        }


@dataclass(frozen=True)
class _Gabarit:
    """Le gabarit d'exécution d'une forme candidate, avant l'entrée qui l'emballe."""

    transport: str
    commande: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    secrets: tuple[VariableSecret, ...] = ()


def deriver_mode_auth(secrets: Sequence[VariableSecret]) -> str:
    """Le `mode_auth` **dérivé** des variables du gabarit — jamais deviné.

    Aucune variable → `sans_secret` ; au moins une variable secrète →
    `token_statique`. Des variables dont aucune n'est secrète tombent sur
    `sans_secret` : les modes classent *comment un secret s'obtient*, et cette
    question n'a pas d'objet quand il n'y en a aucun (c'est la définition même du
    mode dans `MODES_AUTH`). Ce qu'il faut fournir reste porté par `secrets`, que
    l'UI lit pour dresser son formulaire.

    La fonction ne rend **jamais** `appairage` ni `oauth_importe`
    (`MODES_CURATION`) : ces deux-là décrivent une procédure humaine que le
    registre ne déclare pas, et les deviner reviendrait à écraser une curation.
    """
    return "token_statique" if any(variable.secret for variable in secrets) else "sans_secret"


def traduire_entree(entree: EntreeAmont) -> Traduction:
    """Traduit une entrée du miroir (`maestro.agents.mcp_amont.EntreeAmont`).

    Le verbe que la fédération (lot 3) appelle : il ajoute au passage la seule
    question que le document ne porte pas — le `status`, qui vit dans le `_meta`
    de l'amont et que le miroir extrait à côté. Une entrée `deleted` est refusée
    ici plutôt que traduite : le miroir ne devrait jamais en garder (la modération
    amont les retire), et une entrée retirée pour spam ou malware n'a rien à faire
    dans une bibliothèque, fût-elle marquée non curée.
    """
    if entree.statut == STATUT_SUPPRIME:
        return Traduction(
            nom=entree.nom,
            refus=Refus(
                MOTIF_SUPPRIMEE,
                "entrée « deleted » chez l'amont (modération) : elle ne se traduit pas.",
            ),
        )
    return traduire(entree.document)


def traduire(document: Mapping[str, Any]) -> Traduction:
    """Traduit un `server.json` en `EntreeRegistre`, ou rend un refus nommé.

    `document` est le document amont (`EntreeAmont.document`) ; l'enveloppe de
    listing `{"server": …, "_meta": …}` est acceptée telle quelle et déballée,
    pour qu'un appelant qui tient une page brute n'ait pas à la démonter.

    Ne lève jamais : tout ce qui empêche la traduction ressort en `Traduction.refus`.
    """
    brut = _deballe(document)
    schema = _texte(brut.get("$schema"))
    notes: list[str] = []
    if schema and _millesime(schema) not in SCHEMAS_CONNUS:
        notes.append(
            f"schéma amont inconnu ({schema}) : traduit avec les champs connus — "
            "une forme qui aurait changé de nom sortira en refus nommé, pas en entrée bancale."
        )

    nom_amont = _texte(brut.get("name")).strip()
    if not nom_amont:
        return _refuse("", schema, notes, MOTIF_IDENTITE, "document sans « name » exploitable.")
    slug = _slug(nom_amont)
    if not slug:
        return _refuse(
            nom_amont,
            schema,
            notes,
            MOTIF_IDENTITE,
            f"« {nom_amont} » ne donne aucun identifiant utilisable "
            "(slug [a-z0-9_-] attendu).",
        )
    if slug in ID_RESERVES:
        return _refuse(
            nom_amont,
            schema,
            notes,
            MOTIF_IDENTITE,
            f"l'identifiant dérivé {slug!r} est réservé par une route de l'API : "
            "l'entrée serait injoignable.",
        )

    candidats = _candidats(brut)
    if not candidats:
        return _refuse(
            nom_amont,
            schema,
            notes,
            MOTIF_SANS_FORME,
            "ni « remotes » ni « packages » : rien à monter.",
        )

    prefixe = _prefixe(slug)
    version_serveur = _texte(brut.get("version"))
    causes: list[str] = []
    motif_premier = ""
    for etiquette, forme, candidat in candidats:
        essai: list[str] = []
        try:
            if forme == "remote":
                gabarit = _remote(candidat, etiquette=etiquette, prefixe=prefixe, notes=essai)
            else:
                gabarit = _package(
                    candidat,
                    etiquette=etiquette,
                    prefixe=prefixe,
                    version_serveur=version_serveur,
                    notes=essai,
                )
        except _Irreductible as exc:
            motif_premier = motif_premier or exc.motif
            causes.append(_situe(etiquette, exc.cause))
            continue

        entree = _entree(brut, slug=slug, nom_amont=nom_amont, gabarit=gabarit)
        try:
            entree.vers_serveur()
        except ValueError as exc:
            motif_premier = motif_premier or MOTIF_VALIDATION
            causes.append(_situe(etiquette, str(exc)))
            continue

        notes.extend(essai)
        notes.extend(f"{ecarte} (écartée au profit de {etiquette})" for ecarte in causes)
        return Traduction(
            nom=nom_amont,
            schema=schema,
            entree=entree,
            avertissements=_uniques(notes),
        )

    # Une seule forme déclarée : sa cause *est* le refus. En dénombrer une seule
    # ferait passer un diagnostic précis pour un bilan — c'est le cas courant, et
    # c'est celui qu'on lit le plus.
    return _refuse(
        nom_amont,
        schema,
        notes,
        motif_premier or MOTIF_VALIDATION,
        causes[0]
        if len(causes) == 1
        else f"aucune des {len(causes)} formes déclarées ne se traduit : "
        + " · ".join(causes),
    )


def _candidats(brut: Mapping[str, Any]) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
    """Les formes à essayer, **`remotes` d'abord** (docs/21 §3.4 : rien à exécuter).

    L'ordre est le contenu de la décision : un endpoint distant se vérifie par son
    URL et ne fait rien tourner sur le poste, là où un paquet exécute du code tiré
    d'un registre public. À forme égale, l'ordre de déclaration amont est conservé.
    """
    formes: list[tuple[str, str, Mapping[str, Any]]] = []
    for rang, remote in enumerate(_liste(brut, "remotes")):
        if isinstance(remote, dict):
            formes.append((f"remotes[{rang}]", "remote", remote))
    for rang, paquet in enumerate(_liste(brut, "packages")):
        if isinstance(paquet, dict):
            formes.append((f"packages[{rang}]", "package", paquet))
    return tuple(formes)


def _remote(
    brut: Mapping[str, Any], *, etiquette: str, prefixe: str, notes: list[str]
) -> _Gabarit:
    """Un `remotes[]` → un endpoint `http`/`sse` avec son `url` et ses `headers`."""
    declare = _alias(brut, "type", "transport_type", "transportType")
    transport = TRANSPORTS_DISTANTS.get(declare.strip().casefold())
    if transport is None:
        raise _Irreductible(
            MOTIF_TRANSPORT,
            f"transport distant non supporté : {declare or '(absent)'!r} "
            f"(attendu : {', '.join(sorted(set(TRANSPORTS_DISTANTS.values())))}).",
        )

    url = _texte(brut.get("url")).strip()
    if not url:
        raise _Irreductible(MOTIF_VALIDATION, "endpoint sans « url ».")
    if _GABARIT.search(url):
        # `resolus` ne traverse ni `url` ni `args` : substituer ici produirait une
        # référence que personne ne résoudrait, et la laisser telle quelle une URL
        # trouée. Les deux sont pires qu'un refus qui se lit.
        raise _Irreductible(
            MOTIF_URL,
            f"variable en URL, non résoluble : {url} — "
            "les ${VAR} ne sont résolus que dans env et headers (docs/21 §3.4).",
        )

    headers: dict[str, str] = {}
    secrets: list[VariableSecret] = []
    for rang, entete in enumerate(_liste(brut, "headers")):
        declaree = _clef_valeur(
            entete, prefixe=prefixe, ou=f"{etiquette}.headers[{rang}]", notes=notes
        )
        if declaree is None:
            continue
        cle, valeur, variables = declaree
        headers[cle] = valeur
        secrets.extend(variables)

    return _Gabarit(
        transport=transport, url=url, headers=headers, secrets=_uniques_secrets(secrets)
    )


def _package(
    brut: Mapping[str, Any],
    *,
    etiquette: str,
    prefixe: str,
    version_serveur: str,
    notes: list[str],
) -> _Gabarit:
    """Un `packages[]` → une commande `stdio` **à version épinglée**."""
    transport = _texte(_sous(brut, "transport").get("type")).strip().casefold()
    if transport and transport != "stdio":
        raise _Irreductible(
            MOTIF_TRANSPORT,
            f"paquet à transport {transport!r} : un paquet qu'il faut lancer *puis* "
            "joindre par une URL n'est pas exprimable ici (une déclaration porte "
            "l'un ou l'autre, jamais les deux).",
        )

    registre = _alias(brut, "registryType", "registry_type", "registry_name").strip().casefold()
    runtime = _alias(brut, "runtimeHint", "runtime_hint").strip().casefold()
    runtime = runtime or REGISTRES_SUPPORTES.get(registre, "")
    if registre not in REGISTRES_SUPPORTES or runtime not in RUNTIMES_SUPPORTES:
        raise _Irreductible(
            MOTIF_REGISTRE,
            f"registryType {registre or '(absent)'!r} / runtimeHint "
            f"{runtime or '(absent)'!r} non supporté "
            f"(attendu : {', '.join(sorted(REGISTRES_SUPPORTES))}).",
        )

    identifiant = (_alias(brut, "identifier", "name")).strip()
    if not identifiant:
        raise _Irreductible(MOTIF_VALIDATION, "paquet sans « identifier ».")

    version = (_texte(brut.get("version")) or version_serveur).strip()
    if not _EPINGLEE.match(version):
        raise _Irreductible(
            MOTIF_VERSION,
            f"version non épinglée : {version or '(absente)'!r} — une étiquette "
            "flottante retire à la fédération le seul argument qui la rend sûre.",
        )

    avant = _arguments(
        _liste(brut, "runtimeArguments", "runtime_arguments"),
        ou=f"{etiquette}.runtimeArguments",
        notes=notes,
    )
    apres = _arguments(
        _liste(brut, "packageArguments", "package_arguments"),
        ou=f"{etiquette}.packageArguments",
        notes=notes,
    )

    env: dict[str, str] = {}
    secrets: list[VariableSecret] = []
    for rang, variable in enumerate(
        _liste(brut, "environmentVariables", "environment_variables")
    ):
        declaree = _clef_valeur(
            variable,
            prefixe=prefixe,
            ou=f"{etiquette}.environmentVariables[{rang}]",
            notes=notes,
        )
        if declaree is None:
            continue
        cle, valeur, variables = declaree
        env[cle] = valeur
        secrets.extend(variables)

    return _Gabarit(
        transport="stdio",
        commande=runtime,
        # `uvx <paquet>@<version>` suppose que le script d'entrée porte le nom du
        # paquet — la même hypothèse que le seed curé (`uvx mcp-server-fetch`), et
        # la seule que le registre permette : il ne déclare pas de point d'entrée.
        args=(*RUNTIMES_SUPPORTES[runtime], *avant, f"{identifiant}@{version}", *apres),
        env=env,
        secrets=_uniques_secrets(secrets),
    )


def _arguments(bruts: Sequence[Any], *, ou: str, notes: list[str]) -> tuple[str, ...]:
    """Les arguments **littéraux** d'un paquet ; une valeur nécessaire y est un refus.

    `resolus` ne traverse jamais `args` : un argument qui attend une saisie ne
    pourra jamais la recevoir. On émet donc ce qui est littéral (`--stdio`,
    `mcp`, `--port 8080`), on saute ce qui est facultatif et vide — en le disant
    —, et on **refuse** dès qu'une valeur requise manque.

    Un argument requis sans valeur est refusé même s'il *ressemble* à un simple
    drapeau : rien dans le schéma ne distingue à coup sûr `--verbose` (complet
    tel quel) de `--api-key` (amputé de sa valeur), et se tromper dans ce sens
    produirait exactement l'entrée à moitié déclarée que ce module interdit.
    """
    rendus: list[str] = []
    for rang, brut in enumerate(bruts):
        ici = f"{ou}[{rang}]"
        if not isinstance(brut, dict):
            raise _Irreductible(MOTIF_ARGV, f"{ici} : objet attendu.")
        nom = _texte(brut.get("name")).strip()
        genre = _texte(brut.get("type")).strip().casefold() or ("named" if nom else "positional")
        litteral = (_texte(brut.get("value")) or _texte(brut.get("default"))).strip()
        gabarite = bool(_GABARIT.search(litteral)) or bool(_sous(brut, "variables"))
        if gabarite:
            raise _Irreductible(
                MOTIF_ARGV,
                f"{ici} : variable en argv, non résoluble "
                "(les ${VAR} ne sont résolus que dans env et headers).",
            )
        if genre == "named" and not nom:
            raise _Irreductible(MOTIF_ARGV, f"{ici} : argument nommé sans « name ».")
        if litteral:
            rendus.extend((nom, litteral) if genre == "named" else (litteral,))
            continue
        if _requis(brut):
            raise _Irreductible(
                MOTIF_ARGV,
                f"{ici} : valeur nécessaire en argv, non résoluble "
                f"({nom or 'argument positionnel'}).",
            )
        notes.append(f"{ici} : argument facultatif sans valeur — omis du gabarit.")
    return tuple(rendus)


def _clef_valeur(
    brut: object, *, prefixe: str, ou: str, notes: list[str]
) -> tuple[str, str, tuple[VariableSecret, ...]] | None:
    """Une `KeyValueInput` amont → `(clé, valeur du gabarit, variables à fournir)`.

    Rend `None` quand la déclaration n'a rien à apporter au gabarit — une
    variable ni requise ni secrète et sans valeur —, et le **dit** dans `notes`.
    Ce n'est pas une omission de confort : une référence `${VAR}` dans le gabarit
    *signifie* « requise » pour `resolus` (une variable absente rend le serveur
    non montable), donc y placer une variable facultative changerait sa nature.

    Une variable **secrète** est en revanche toujours déclarée, `isRequired`
    absent compris : le défaut de schéma est `false`, mais un serveur qui annonce
    lire un secret en a besoin, et l'omettre le ferait passer pour `sans_secret`.
    """
    if not isinstance(brut, dict):
        raise _Irreductible(MOTIF_VALIDATION, f"{ou} : objet attendu.")
    cle = _texte(brut.get("name")).strip()
    if not cle:
        raise _Irreductible(MOTIF_VALIDATION, f"{ou} : déclaration sans « name ».")

    secret = _drapeau(brut, "isSecret", "is_secret", defaut=False)
    litteral = _texte(brut.get("value")) or _texte(brut.get("default"))
    if litteral:
        marques = _GABARIT.findall(litteral)
        if not marques:
            if secret:
                notes.append(
                    f"{ou} : valeur littérale déclarée secrète — laissée telle quelle "
                    "(le registre interdit les secrets en clair dans « value »)."
                )
            return cle, litteral, ()
        table = _sous(brut, "variables")
        rendu = litteral
        variables: list[VariableSecret] = []
        for marque in marques:
            declaration = table.get(marque)
            connue = isinstance(declaration, dict)
            decl: Mapping[str, Any] = declaration if isinstance(declaration, dict) else {}
            if not connue:
                notes.append(
                    f"{ou} : gabarit {{{marque}}} non déclaré dans « variables » — "
                    "traité comme une variable secrète requise."
                )
            nom_var = _variable(prefixe, marque)
            rendu = rendu.replace("{" + marque + "}", "${" + nom_var + "}")
            variables.append(
                VariableSecret(
                    cle=nom_var,
                    description=_texte(decl.get("description")),
                    secret=_drapeau(decl, "isSecret", "is_secret", defaut=not connue),
                )
            )
        return cle, rendu, tuple(variables)

    if not (_requis(brut) or secret):
        notes.append(f"{ou} : variable facultative sans valeur — omise du gabarit.")
        return None

    nom_var = _variable(prefixe, cle)
    return (
        cle,
        "${" + nom_var + "}",
        (
            VariableSecret(
                cle=nom_var,
                description=_texte(brut.get("description")),
                secret=secret,
            ),
        ),
    )


def _entree(
    brut: Mapping[str, Any], *, slug: str, nom_amont: str, gabarit: _Gabarit
) -> EntreeRegistre:
    """Emballe un gabarit traduit dans l'`EntreeRegistre` que la bibliothèque sert.

    `nom` et `editeur` découpent le nom amont sur son dernier `/` : ils le
    recomposent au caractère près, donc rien n'est perdu et rien n'est embelli.
    `tags` reste vide et `popularite` à zéro — l'amont ne déclare ni l'un ni
    l'autre, et les fabriquer serait fabriquer de la métadonnée (`rechercher`
    fouille de toute façon l'id, le nom, l'éditeur et la description).
    """
    editeur, _, court = nom_amont.rpartition("/")
    depot = _sous(brut, "repository")
    return EntreeRegistre(
        id=slug,
        nom=court or nom_amont,
        description=_texte(brut.get("description")),
        mode_auth=deriver_mode_auth(gabarit.secrets),
        transport=gabarit.transport,
        commande=gabarit.commande,
        args=gabarit.args,
        url=gabarit.url,
        env=gabarit.env,
        headers=gabarit.headers,
        secrets=gabarit.secrets,
        procedure_url=(
            _alias(brut, "websiteUrl", "website_url") or _texte(depot.get("url"))
        ),
        # Une entrée dont un humain doit fournir une valeur est **optionnelle**
        # (#125) : sans la valeur, la voie est omise du montage au lieu de faire
        # échouer la tâche. Sans variable, il n'y a rien à attendre.
        optionnel=bool(gabarit.secrets),
        editeur=editeur,
    )


def _refuse(
    nom: str, schema: str, notes: list[str], motif: str, cause: str
) -> Traduction:
    """Un refus, avec les avertissements déjà accumulés — ils restent lisibles."""
    return Traduction(
        nom=nom,
        schema=schema,
        refus=Refus(motif, cause),
        avertissements=_uniques(notes),
    )


def _situe(etiquette: str, cause: str) -> str:
    """La cause préfixée de la forme d'où elle vient — sans la répéter deux fois.

    Les causes nées au fond d'un argument ou d'un en-tête portent déjà le chemin
    complet (`packages[0].packageArguments[1] : …`) ; celles qui jugent la forme
    entière (transport, version, registre) n'en portent aucun.
    """
    return cause if cause.startswith(etiquette) else f"{etiquette} — {cause}"


def _deballe(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Le document serveur, que l'appelant tienne le `server.json` ou son enveloppe."""
    interne = document.get("server")
    if isinstance(interne, dict) and "name" not in document:
        return interne
    return document


def _millesime(schema: str) -> str:
    """La date d'un `$schema` (`…/schemas/2025-12-11/server.schema.json`) — «» si absente."""
    trouve = re.search(r"(\d{4}-\d{2}-\d{2})", schema)
    return trouve.group(1) if trouve else ""


def _slug(nom: str) -> str:
    """Le nom amont replié en identifiant de registre (`^[a-z0-9][a-z0-9_-]*$`).

    `io.github.alice/mon-serveur` → `io-github-alice-mon-serveur`. Le namespace
    est conservé : c'est lui qui rend l'identifiant injectif et qui évite qu'une
    entrée découverte se substitue à une entrée curée du seed.
    """
    plat = _TIRETS.sub("-", _HORS_SLUG.sub("-", nom.strip().casefold())).strip("-_")
    while plat and not ("a" <= plat[0] <= "z" or plat[0].isdigit()):
        plat = plat[1:]
    return plat


def _prefixe(slug: str) -> str:
    """Le préfixe des variables d'une entrée — `MCP_IO_GITHUB_ALICE_MON_SERVEUR`."""
    return f"{PREFIXE_VARIABLE}_{_HORS_ENV.sub('_', slug.upper())}"


def _variable(prefixe: str, cle: str) -> str:
    """Le nom de la variable `${VAR}` d'une clé amont, namespacée par le serveur."""
    return f"{prefixe}_{_HORS_ENV.sub('_', cle.upper()).strip('_')}"


def _liste(brut: Mapping[str, Any], *cles: str) -> tuple[Any, ...]:
    """La première des `cles` qui porte une liste (alias de schéma), sinon ()."""
    for cle in cles:
        valeur = brut.get(cle)
        if isinstance(valeur, list):
            return tuple(valeur)
    return ()


def _sous(brut: Mapping[str, Any], cle: str) -> Mapping[str, Any]:
    """Le sous-objet `cle` s'il en est un, sinon un objet vide (jamais None)."""
    valeur = brut.get(cle)
    return valeur if isinstance(valeur, dict) else {}


def _alias(brut: Mapping[str, Any], *cles: str) -> str:
    """La première des `cles` qui porte une chaîne — la tolérance aux millésimes."""
    for cle in cles:
        valeur = brut.get(cle)
        if isinstance(valeur, str) and valeur:
            return valeur
    return ""


def _drapeau(brut: Mapping[str, Any], *cles: str, defaut: bool) -> bool:
    """Un booléen amont sous l'un de ses alias — `defaut` s'il n'est pas booléen."""
    for cle in cles:
        valeur = brut.get(cle)
        if isinstance(valeur, bool):
            return valeur
    return defaut


def _requis(brut: Mapping[str, Any]) -> bool:
    """`isRequired` de l'amont (défaut `false`, comme le schéma le prévoit)."""
    return _drapeau(brut, "isRequired", "is_required", defaut=False)


def _texte(valeur: object) -> str:
    """`valeur` si c'est une chaîne, sinon la chaîne vide — jamais un `str(dict)`."""
    return valeur if isinstance(valeur, str) else ""


def _uniques(lignes: Sequence[str]) -> tuple[str, ...]:
    """Les lignes dédoublonnées, ordre d'apparition préservé."""
    vues: list[str] = []
    for ligne in lignes:
        if ligne not in vues:
            vues.append(ligne)
    return tuple(vues)


def _uniques_secrets(secrets: Sequence[VariableSecret]) -> tuple[VariableSecret, ...]:
    """Les variables dédoublonnées **par clé**, la première déclaration l'emportant."""
    vues: dict[str, VariableSecret] = {}
    for secret in secrets:
        vues.setdefault(secret.cle, secret)
    return tuple(vues.values())
