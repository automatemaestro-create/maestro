"""Sonde du poste — ce qui est déjà installé ici (ticket #487).

Le fournisseur et le modèle d'un agent étaient deux champs texte libre : rien ne
disait à l'utilisateur ce que **sa machine** a déjà. Cette sonde répond à cette
question-là, et à elle seule — le registre (`maestro.providers.registry`) dit ce
que **Maestro** sait faire, la sonde ce qui est **présent ici** ; c'est
`maestro.controltower.fournisseurs` qui marie les deux en un catalogue unique.

Elle vit **hors de `maestro.providers`** à dessein, et pas par goût du rangement :
importer ce paquet-là tire `claude_agent_sdk` à l'import, prix que tout le dépôt
paie au premier usage et jamais au chargement (les imports de
`provider_from_settings` sont paresseux partout). L'API doit pouvoir connaître la
sonde sans payer le SDK pour autant ; ici, elle ne dépend que de la bibliothèque
standard et du client HTTP.

**Trois familles**, et rien d'autre : les **CLI d'agents** résolus sur le `PATH`,
les **serveurs de modèles locaux** qui répondent (Ollama, tout endpoint compatible
OpenAI en boucle locale) et les **clés de fournisseurs** présentes dans
l'environnement.

**Gratuite et sans effet de bord — au sens fort.** Elle ne démarre rien,
n'installe rien, n'écrit rien, et un poste nu rend un rapport vide sans erreur.
Trois conséquences qui sont des décisions, pas des oublis :

1. **Aucun binaire n'est exécuté**, pas même pour lire sa version. Résoudre un
   nom sur le `PATH` est une lecture ; lancer ce qu'on vient d'y trouver ne l'est
   pas, et c'est très exactement le prix que
   [docs/28 §7](../docs/28-decision-frontiere-execution-run.md) range dans « ce
   que nous ne payons pas ». La version est donc **dite inconnue** plutôt que
   devinée (critère 4).
2. **Rien n'est détecté par le nom d'un processus** — la leçon de #213 : un
   `claude.exe` repéré au nom peut être la session interactive de l'utilisateur.
   On résout un exécutable, on ne regarde pas qui tourne.
3. **Seule la boucle locale est sondée.** Un endpoint distant configuré est
   *signalé* mais jamais appelé : le joindre enverrait la clé sur le réseau, et
   l'appeler pour de bon coûterait de l'argent.

**Ce que la sonde ne peut pas savoir, elle le dit.** Chaque constat porte son
`incertitude`, et le rapport porte les incertitudes qui pèsent sur les *absences*
— au premier rang desquelles le `PATH` : celui du process qui sert l'API n'est
pas celui du shell de l'utilisateur (la panne déjà payée par
`scripts/mcp/playwright-mcp.mjs`), donc « rien trouvé » ne vaut pas « rien
installé ».

**Injectable de bout en bout** : `resolveur`, `environ` et `lecteur` sont des
paramètres. C'est ce qui permet à la suite de l'exercer sans `PATH`, sans réseau
et sans machine particulière — `tests/conftest.py` (#195) exige qu'aucun test
n'ait besoin d'un backend.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx2

#: Un exécutable trouvé sur le `PATH`.
GENRE_CLI = "cli"
#: Un serveur de modèles qui écoute (et répond, ou non) sur la boucle locale.
GENRE_SERVEUR = "serveur_local"
#: Une variable d'environnement porteuse d'un credential de fournisseur.
GENRE_CLE = "cle"

#: Délai d'un appel de sonde. Court à dessein : la cible est sur la boucle
#: locale, et une sonde qui fait attendre l'ouverture d'un formulaire a manqué
#: son but. Un serveur plus lent que ça est rendu « écoute sans répondre » —
#: ce qui est le constat utile, pas une panne de la sonde.
DELAI_S = 1.5

#: Ce que la sonde ne saura jamais d'un binaire sans l'exécuter.
_INCERTITUDE_VERSION = (
    "version inconnue — la lire demanderait d'exécuter le binaire, "
    "ce que la sonde ne fait pas"
)

#: Ce qu'une variable présente ne prouve pas.
_INCERTITUDE_CLE = (
    "présence seulement : une clé présente ne prouve pas qu'elle est valide, "
    "et le vérifier demanderait un appel facturé"
)

#: Pourquoi un endpoint distant configuré n'est pas joint.
_INCERTITUDE_DISTANT = (
    "non sondé : joindre un endpoint distant enverrait la clé sur le réseau — "
    "la sonde s'en tient à la boucle locale"
)

#: L'incertitude qui pèse sur les **absences**, donc sur le rapport entier.
INCERTITUDE_PATH = (
    "les CLI sont résolus sur le `PATH` du process qui sert l'API, qui n'est pas "
    "toujours celui de votre terminal : un outil absent d'ici peut être installé "
    "sur la machine"
)

#: Idem pour les serveurs : seule la boucle locale est regardée.
INCERTITUDE_PORTEE = (
    "seuls la boucle locale et l'environnement de ce process sont regardés — "
    "rien n'est démarré, rien n'est installé, rien n'est écrit"
)

#: Hôtes tenus pour « cette machine ».
_HOTES_LOCAUX = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"})


@dataclass(frozen=True)
class Constat:
    """Un fait mesuré sur le poste : ce qui a été trouvé, et ce qu'on en sait.

    N'existe que pour ce qui est **présent** — un poste nu rend zéro constat, pas
    une liste de « non trouvé » (critère 2). `utilisable` distingue en revanche
    le présent-et-prêt du présent-mais-empêché : un serveur qui écoute sans
    répondre, un endpoint configuré hors de portée de la sonde.
    """

    #: `GENRE_CLI`, `GENRE_SERVEUR` ou `GENRE_CLE`.
    genre: str
    #: Identifiant stable, unique dans un rapport (`cli:claude`, `cle:OPENAI_API_KEY`).
    cle: str
    #: Ce que l'utilisateur reconnaît (« Claude Code », « Ollama »).
    libelle: str
    #: Le fournisseur Maestro que ce constat sert, ou `None` s'il n'en sert aucun.
    fournisseur: str | None
    #: Vrai quand rien de connu n'empêche de s'en servir.
    utilisable: bool
    #: En clair : ce qui le rend utilisable, ou ce qui l'en empêche.
    detail: str
    #: Où il a été trouvé — chemin résolu, racine de l'endpoint, nom de variable.
    origine: str | None = None
    #: Les modèles que ce constat sert (un serveur local les nomme ; le reste non).
    modeles: tuple[str, ...] = ()
    #: Ce que la sonde ne peut pas savoir de **ce** constat.
    incertitude: str | None = None


@dataclass(frozen=True)
class RapportSonde:
    """Ce que la sonde a trouvé, et ce qu'elle n'a pas pu savoir."""

    #: Les constats, dans l'ordre CLI → serveurs → clés.
    constats: tuple[Constat, ...] = ()
    #: Les incertitudes qui pèsent sur les **absences**, donc sur tout le rapport.
    incertitudes: tuple[str, ...] = ()

    def par_fournisseur(self, nom: str) -> tuple[Constat, ...]:
        """Les constats qui servent le fournisseur `nom`."""
        return tuple(c for c in self.constats if c.fournisseur == nom)

    @property
    def hors_registre(self) -> tuple[Constat, ...]:
        """Les constats qui ne servent aucun fournisseur Maestro."""
        return tuple(c for c in self.constats if c.fournisseur is None)


@dataclass(frozen=True)
class ReponseLocale:
    """Ce qu'une sonde HTTP locale a obtenu — ou n'a pas obtenu."""

    #: Faux quand rien n'écoute (connexion refusée, hôte inconnu, délai dépassé).
    joignable: bool
    #: Le code HTTP, quand il y en a eu un.
    statut: int | None = None
    #: Le corps brut, tel quel (jamais décodé deux fois).
    corps: str = ""
    #: La cause, en clair, quand `joignable` est faux ou le statut mauvais.
    erreur: str | None = None


#: Résout un nom d'exécutable en chemin absolu, ou `None` (signature de `shutil.which`).
Resolveur = Callable[[str], str | None]

#: Interroge une URL locale en lecture seule et rend ce qu'elle a obtenu.
Lecteur = Callable[[str, float], Awaitable[ReponseLocale]]


@dataclass(frozen=True)
class _CliConnu:
    """Un CLI d'agent qu'on sait reconnaître, et ce qu'il vaut pour Maestro."""

    commande: str
    libelle: str
    #: Le fournisseur Maestro dont il est le runtime, ou `None` — voir docs/34.
    fournisseur: str | None
    role: str


#: Les CLI cherchés. `claude` est le runtime du fournisseur `claude` (le SDK le
#: lance en sous-processus) ; les autres sont là **pour être distingués** : les
#: voir sans les prétendre branchés est exactement ce que demande le critère 3, et
#: docs/34 a décidé de ne pas les brancher (« la porte est décrite, elle reste
#: fermée »). Les annoncer utilisables serait le seul vrai mensonge possible ici.
_CLI_CONNUS: tuple[_CliConnu, ...] = (
    _CliConnu(
        commande="claude",
        libelle="Claude Code",
        fournisseur="claude",
        role="runtime des agents Claude — le SDK le lance en sous-processus",
    ),
    _CliConnu(
        commande="codex",
        libelle="Codex CLI",
        fournisseur=None,
        role="agent CLI tiers — non branché (docs/34)",
    ),
    _CliConnu(
        commande="gemini",
        libelle="Gemini CLI",
        fournisseur=None,
        role="agent CLI tiers — non branché (docs/34)",
    ),
    _CliConnu(
        commande="opencode",
        libelle="opencode",
        fournisseur=None,
        role="agent CLI tiers — non branché (docs/34)",
    ),
    _CliConnu(
        commande="cursor-agent",
        libelle="Cursor Agent",
        fournisseur=None,
        role="agent CLI tiers — non branché (docs/34)",
    ),
)


@dataclass(frozen=True)
class _CleConnue:
    """Une variable d'environnement porteuse d'un credential, et ce qu'elle ouvre."""

    variable: str
    fournisseur: str
    mode: str


#: Les credentials cherchés dans l'environnement. **Jamais la valeur** : seule la
#: présence est rendue, et elle ne quitte pas cette machine.
_CLES_CONNUES: tuple[_CleConnue, ...] = (
    _CleConnue("ANTHROPIC_API_KEY", "claude", "clé API"),
    _CleConnue("ANTHROPIC_AUTH_TOKEN", "claude", "jeton bearer (passerelle)"),
    _CleConnue("CLAUDE_CODE_OAUTH_TOKEN", "claude", "abonnement Claude Code"),
    _CleConnue("OPENAI_API_KEY", "openai", "clé API"),
)

#: Racine par défaut d'Ollama (#113). `OLLAMA_HOST` la déplace — c'est la
#: convention d'Ollama lui-même, pas une variable que Maestro invente.
OLLAMA_DEFAUT = "http://127.0.0.1:11434"


class SondePoste:
    """La sonde, avec ses trois seams d'injection.

    Aucun état, aucun cache : deux appels rendent deux mesures. C'est voulu —
    un cache serait une écriture, et une réponse périmée sur « qu'est-ce qui
    tourne ici ? » est pire qu'une mesure un peu plus lente.
    """

    def __init__(
        self,
        *,
        resolveur: Resolveur | None = None,
        environ: Mapping[str, str] | None = None,
        lecteur: Lecteur | None = None,
        delai_s: float = DELAI_S,
    ) -> None:
        self._resolveur: Resolveur = resolveur if resolveur is not None else shutil.which
        self._environ = environ
        self._lecteur: Lecteur = lecteur if lecteur is not None else lire_locale
        self._delai_s = delai_s

    async def rapport(self) -> RapportSonde:
        """Le rapport complet — CLI, serveurs locaux, clés — et ses incertitudes."""
        environ: Mapping[str, str] = self._environ if self._environ is not None else os.environ
        constats: list[Constat] = []
        constats.extend(self._clis())
        constats.extend(await self._serveurs(environ))
        constats.extend(self._cles(environ))
        return RapportSonde(
            constats=tuple(constats),
            incertitudes=(INCERTITUDE_PATH, INCERTITUDE_PORTEE),
        )

    def _clis(self) -> list[Constat]:
        """Les CLI d'agents résolus sur le `PATH` — résolution seule, aucun lancement."""
        trouves: list[Constat] = []
        for connu in _CLI_CONNUS:
            chemin = self._resolveur(connu.commande)
            if not chemin:
                continue
            branche = connu.fournisseur is not None
            trouves.append(
                Constat(
                    genre=GENRE_CLI,
                    cle=f"{GENRE_CLI}:{connu.commande}",
                    libelle=connu.libelle,
                    fournisseur=connu.fournisseur,
                    utilisable=branche,
                    detail=connu.role,
                    origine=chemin,
                    incertitude=_INCERTITUDE_VERSION,
                )
            )
        return trouves

    async def _serveurs(self, environ: Mapping[str, str]) -> list[Constat]:
        """Les serveurs de modèles de la boucle locale, sondés en lecture seule."""
        trouves: list[Constat] = []
        vus: set[tuple[str, int | None]] = set()

        racine_ollama = _racine_ollama(environ)
        vus.add(_empreinte(racine_ollama))
        constat = await self._ollama(racine_ollama)
        if constat is not None:
            trouves.append(constat)

        configure = (environ.get("OPENAI_BASE_URL") or "").strip()
        if configure and _empreinte(configure) not in vus:
            trouves.append(await self._compatible(configure))
        return trouves

    async def _ollama(self, racine: str) -> Constat | None:
        """Ollama sur la boucle locale : ses modèles, ou la raison de son silence.

        Un `OLLAMA_HOST` pointant ailleurs est **nommé et non joint**, comme un
        `OPENAI_BASE_URL` distant : « seule la boucle locale est sondée » est une
        promesse du module, et une exception pour un fournisseur la rendrait
        fausse pour tous.
        """
        if not _est_local(racine):
            return _distant(
                cle="serveur:ollama",
                libelle="Ollama (déclaré hors de ce poste)",
                racine=racine,
                source="OLLAMA_HOST",
            )
        reponse = await self._lecteur(f"{racine.rstrip('/')}/api/tags", self._delai_s)
        if not reponse.joignable:
            return None
        modeles = _noms(reponse.corps, "models", "name")
        prete = reponse.statut == 200 and modeles is not None
        return Constat(
            genre=GENRE_SERVEUR,
            cle="serveur:ollama",
            libelle="Ollama",
            fournisseur="openai",
            utilisable=prete,
            detail=(
                "servi par le fournisseur `openai` — "
                f"MAESTRO_PROVIDER=openai OPENAI_BASE_URL={racine.rstrip('/')}/v1"
                if prete
                else _silence(reponse)
            ),
            origine=racine,
            modeles=modeles or (),
            # #113 : un fournisseur local reste un cas valide du catalogue, mais il
            # ne rapporte aucun coût — le plafond de dépense n'a donc pas de prise.
            incertitude="aucun coût rapporté par ce fournisseur (#113)",
        )

    async def _compatible(self, base: str) -> Constat:
        """L'endpoint compatible OpenAI configuré : sondé s'il est local, sinon nommé."""
        racine = base.rstrip("/")
        if not _est_local(racine):
            return _distant(
                cle="serveur:openai-configure",
                libelle="Endpoint compatible OpenAI (configuré)",
                racine=racine,
                source="OPENAI_BASE_URL",
            )
        reponse = await self._lecteur(f"{racine}/models", self._delai_s)
        modeles = _noms(reponse.corps, "data", "id") if reponse.joignable else None
        prete = reponse.joignable and reponse.statut == 200 and modeles is not None
        return Constat(
            genre=GENRE_SERVEUR,
            cle="serveur:openai-configure",
            libelle="Endpoint compatible OpenAI (local)",
            fournisseur="openai",
            utilisable=prete,
            detail=(
                "déclaré par OPENAI_BASE_URL et servi par le fournisseur `openai`"
                if prete
                else _silence(reponse)
            ),
            origine=racine,
            modeles=modeles or (),
        )

    def _cles(self, environ: Mapping[str, str]) -> list[Constat]:
        """Les credentials présents dans l'environnement — présence seule, jamais la valeur."""
        trouves: list[Constat] = []
        for connue in _CLES_CONNUES:
            if not (environ.get(connue.variable) or "").strip():
                continue
            trouves.append(
                Constat(
                    genre=GENRE_CLE,
                    cle=f"{GENRE_CLE}:{connue.variable}",
                    libelle=f"{connue.variable} ({connue.mode})",
                    fournisseur=connue.fournisseur,
                    utilisable=True,
                    detail=f"renseignée dans l'environnement — {connue.mode}",
                    origine=connue.variable,
                    incertitude=_INCERTITUDE_CLE,
                )
            )
        return trouves


async def lire_locale(url: str, delai_s: float) -> ReponseLocale:
    """Un GET en lecture seule, sans en-tête d'auth, sur la boucle locale.

    Aucune clé n'est envoyée : la sonde constate qu'un serveur répond, elle ne
    s'authentifie nulle part. Toute panne réseau devient un `ReponseLocale`
    plutôt qu'une exception — « rien n'écoute » est un résultat, pas une erreur
    de la sonde (critère 2).

    ⚠ **Deux délais dépassés, deux verdicts opposés**, et les confondre inverse
    le constat. Un délai à la **connexion** veut dire que rien n'a répondu au
    `SYN` : personne n'écoute — c'est ce que rend un port fermé sous Windows,
    là où Linux refuse franchement la connexion. Un délai à la **lecture** veut
    dire que la connexion a été acceptée et que la réponse n'est jamais venue :
    le serveur est là, et muet — le cas dégradé que le ticket nomme. Les ranger
    tous deux sous « présent » ferait apparaître un Ollama sur toute machine qui
    n'en a pas.
    """
    try:
        async with httpx2.AsyncClient(timeout=delai_s) as client:
            reponse = await client.get(url)
    except (httpx2.ConnectTimeout, httpx2.ConnectError, httpx2.PoolTimeout) as exc:
        return ReponseLocale(joignable=False, erreur=str(exc) or "connexion impossible")
    except httpx2.TimeoutException:
        return ReponseLocale(joignable=True, erreur="délai dépassé sans réponse")
    except httpx2.HTTPError as exc:
        return ReponseLocale(joignable=False, erreur=str(exc))
    return ReponseLocale(joignable=True, statut=reponse.status_code, corps=reponse.text)


def _distant(*, cle: str, libelle: str, racine: str, source: str) -> Constat:
    """Un endpoint déclaré hors de ce poste : nommé, jamais joint.

    Il compte comme un constat — l'utilisateur l'a configuré, le taire donnerait
    à lire « rien de ce côté-là ». Mais `utilisable` reste faux : la sonde n'a
    rien vérifié, et prétendre l'inverse serait exactement la devinette que le
    critère 4 interdit.
    """
    return Constat(
        genre=GENRE_SERVEUR,
        cle=cle,
        libelle=libelle,
        fournisseur="openai",
        utilisable=False,
        detail=f"endpoint distant, déclaré par {source}",
        origine=racine,
        incertitude=_INCERTITUDE_DISTANT,
    )


def _silence(reponse: ReponseLocale) -> str:
    """Dit pourquoi un serveur qui écoute n'est pas utilisable pour autant."""
    if reponse.erreur:
        return f"écoute mais ne répond pas : {reponse.erreur}"
    if reponse.statut is not None and reponse.statut != 200:
        return f"écoute mais répond {reponse.statut}"
    return "écoute mais sa réponse n'est pas celle attendue"


def _noms(corps: str, tableau: str, champ: str) -> tuple[str, ...] | None:
    """Les noms de modèles d'une réponse, ou `None` si la forme n'est pas la bonne.

    `None` et `()` ne disent pas la même chose : un serveur hors dialecte n'est
    pas un serveur sans modèle.
    """
    try:
        charge: Any = json.loads(corps)
    except ValueError:
        return None
    if not isinstance(charge, dict):
        return None
    entrees = charge.get(tableau)
    if not isinstance(entrees, list):
        return None
    noms = [
        e[champ]
        for e in entrees
        if isinstance(e, dict) and isinstance(e.get(champ), str) and e[champ].strip()
    ]
    return tuple(dict.fromkeys(noms))


def _racine_ollama(environ: Mapping[str, str]) -> str:
    """La racine d'Ollama : `OLLAMA_HOST` s'il est posé, sinon le défaut d'Ollama."""
    brut = (environ.get("OLLAMA_HOST") or "").strip()
    if not brut:
        return OLLAMA_DEFAUT
    if "://" not in brut:
        brut = f"http://{brut}"
    return brut.rstrip("/")


def _est_local(url: str) -> bool:
    """Vrai quand l'URL vise cette machine — le seul périmètre que la sonde joint."""
    hote = urlsplit(url).hostname
    return hote is not None and (hote in _HOTES_LOCAUX or hote.startswith("127."))


def _empreinte(url: str) -> tuple[str, int | None]:
    """Identifie un endpoint par sa machine et son port, pas par son écriture.

    `http://localhost:11434/v1` et `http://127.0.0.1:11434` sont le même serveur :
    les rendre deux fois donnerait à l'utilisateur deux entrées pour un service.
    """
    decoupe = urlsplit(url)
    hote = decoupe.hostname or ""
    return ("local" if _est_local(url) else hote, decoupe.port)


#: Le rapport d'un poste nu — utile aux tests et aux appelants sans sonde.
RAPPORT_VIDE = RapportSonde(incertitudes=(INCERTITUDE_PATH, INCERTITUDE_PORTEE))

__all__ = [
    "DELAI_S",
    "GENRE_CLE",
    "GENRE_CLI",
    "GENRE_SERVEUR",
    "INCERTITUDE_PATH",
    "INCERTITUDE_PORTEE",
    "OLLAMA_DEFAUT",
    "RAPPORT_VIDE",
    "Constat",
    "Lecteur",
    "RapportSonde",
    "ReponseLocale",
    "Resolveur",
    "SondePoste",
    "lire_locale",
]
