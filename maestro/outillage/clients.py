"""Les clients d'agents que la personne utilise, et ce qu'ils font d'`AGENTS.md` (#1295).

Un projet outillé par Maestro a **un seul fichier d'instructions**, `AGENTS.md`. Un pont
(`CLAUDE.md`, `GEMINI.md`) ne s'écrit que pour un client **que la personne utilise** — trouvé
sur le poste avec sa version, ou nommé dans la conversation — et qui **ne lit pas** `AGENTS.md`
nativement à cette version (docs/43 §2.3, qui renverse docs/38 §3.2). Écrire un pont pour un
client que personne n'utilise est une
supposition (C2 de « Rien de figé », docs/41) ; en écrire un pour Claude Code ≥ 2.1.277
**masque** même sa lecture native.

Ce module tient deux choses, et il n'exécute rien — la promesse du paquet (`__init__`) :

- **ce que les clients font d'`AGENTS.md`** (`CLIENTS_CONNUS`) : des **faits extérieurs**,
  vérifiés et datés, chacun avec sa source. Ce n'est pas un catalogue de réponses : un client
  absent de la table reste un client de la personne, et Maestro dit qu'il ne sait pas ce qu'il
  lit plutôt que de lui écrire un pont au hasard. La table bouge avec les clients — docs/38 §7
  dit ce qui la rouvrirait, et `tests/test_outillage_clients.py` rejoue le seul fait qui dépend
  d'une version sur le vrai CLI, quand il est installé ;
- **les formes** (`Client`) et leur lecture depuis ce que la conversation a compris
  (`clients_depuis_texte`), puis leur réunion avec ceux du poste (`reunir`).

La **détection** du poste — résoudre la commande, lire `--version` — lance un processus : elle
vit hors du paquet, dans `maestro.clients_du_poste`, comme les commandes du projet vivent dans
`maestro.sandbox`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: D'où Maestro tient qu'un client est utilisé. `poste` : sa commande est résolue ici, et sa
#: version lue ; `conversation` : la personne l'a nommé. Les deux se disent dans la raison du
#: pont, parce qu'ils ne se contestent pas de la même façon.
SOURCE_POSTE = "poste"
SOURCE_CONVERSATION = "conversation"

#: Une version telle qu'un client la rend (`2.1.281 (Claude Code)`, `0.9.0-preview.1`) : les
#: nombres du début, pointés. Le reste — suffixe, nom du produit — ne décide de rien ici.
_VERSION = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)")

#: La version **telle qu'elle s'écrit après un nom** (`v0.9.0`, `version 2.1.200-beta`) — ce qu'on
#: retire d'un élément de la conversation pour n'en garder que le nom.
_VERSION_ECRITE = re.compile(r"\s*\(?(?:\bv|\bversion\s*)?(?<![\d.])\d+(?:\.\d+)+\S*\)?", re.I)


@dataclass(frozen=True)
class ClientConnu:
    """Un client d'agents dont on sait, **sur pièce**, ce qu'il fait d'`AGENTS.md`.

    `natif_depuis` dit à partir de quelle version il le lit de lui-même : `""` pour toutes,
    `None` pour aucune (par défaut). `pont` est le fichier d'une ligne qui le lui fait lire
    quand il ne le lit pas — `None` pour un client qui n'en a jamais besoin. `masque` : un
    fichier `pont` déjà présent l'empêche de lire `AGENTS.md` nativement (c'est le cas de
    Claude Code : un `CLAUDE.md` est lu **à la place**).

    `noms` sont les façons de le nommer que la conversation rend — sa commande, son nom —, ramenées
    à une clé (`_forme`) : c'est la table lue dans l'autre sens, écrite une fois, pas un lexique.
    """

    cle: str
    libelle: str
    commande: str | None
    natif_depuis: str | None
    pont: str | None
    masque: bool
    source: str
    noms: tuple[str, ...] = ()

    def lit_agents_md(self, version: str | None, *, pont_present: bool = False) -> bool | None:
        """Ce client lit-il `AGENTS.md` de lui-même, à cette version ? `None` : on ne sait pas.

        Un pont déjà présent qui **masque** la lecture native l'emporte sur la version : le
        client lit alors ce fichier-là, et `AGENTS.md` ne lui parvient que si le fichier
        l'importe.
        """
        if self.natif_depuis is None or (self.masque and pont_present):
            return False
        if self.natif_depuis == "":
            return True
        mesuree = cle_de_version(version)
        if mesuree is None:
            return None
        return mesuree >= (cle_de_version(self.natif_depuis) or ())


#: Ce que chaque client fait d'`AGENTS.md` — vérifié le 2026-09-27 sur leurs documentations
#: (docs/38 §2.2 et son renvoi de #1295). Claude Code et Gemini CLI sont les deux seuls à qui un
#: pont peut manquer ; les autres sont là pour que « j'utilise Codex » se lise « aucun pont, il
#: le lit », et non « client inconnu ». Les commandes sont celles que `maestro.poste` cherche.
CLIENTS_CONNUS: tuple[ClientConnu, ...] = (
    ClientConnu(
        cle="claude",
        libelle="Claude Code",
        commande="claude",
        natif_depuis="2.1.277",
        pont="CLAUDE.md",
        masque=True,
        source="https://code.claude.com/docs/en/memory",
        noms=("claude-code",),
    ),
    ClientConnu(
        cle="gemini",
        libelle="Gemini CLI",
        commande="gemini",
        natif_depuis=None,
        pont="GEMINI.md",
        masque=False,
        source="https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md",
        noms=("gemini-cli",),
    ),
    ClientConnu(
        cle="codex",
        libelle="Codex CLI",
        commande="codex",
        natif_depuis="",
        pont=None,
        masque=False,
        source="https://agents.md/",
        noms=("codex-cli", "openai-codex"),
    ),
    ClientConnu(
        cle="cursor",
        libelle="Cursor",
        commande="cursor-agent",
        natif_depuis="",
        pont=None,
        masque=False,
        source="https://agents.md/",
        noms=("cursor-agent",),
    ),
    ClientConnu(
        cle="opencode",
        libelle="opencode",
        commande="opencode",
        natif_depuis="",
        pont=None,
        masque=False,
        source="https://agents.md/",
    ),
    ClientConnu(
        cle="copilot",
        libelle="GitHub Copilot",
        commande=None,
        natif_depuis="",
        pont=None,
        masque=False,
        source="https://agents.md/",
        noms=("github-copilot",),
    ),
)


def _forme(nom: str) -> str:
    """Un nom de client ramené à une clé : minuscules, sans accents, mots liés par des tirets."""
    sans_accents = "".join(
        c
        for c in unicodedata.normalize("NFD", nom.strip().lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "-", sans_accents).strip("-")


#: Chaque nom d'un client connu → le client. Dérivé de la table, jamais écrit à côté.
_PAR_NOM: dict[str, ClientConnu] = {
    _forme(nom): connu
    for connu in CLIENTS_CONNUS
    for nom in (connu.cle, connu.libelle, connu.commande or "", *connu.noms)
    if nom
}


def client_connu(cle: str) -> ClientConnu | None:
    """Le client connu de cette clé (ou de ce nom), `None` s'il ne l'est pas."""
    return _PAR_NOM.get(_forme(cle))


def cle_de_version(version: str | None) -> tuple[int, ...] | None:
    """`"2.1.281 (Claude Code)"` → `(2, 1, 281)` ; `None` quand aucune version ne s'y lit."""
    trouvee = _VERSION.search(version or "")
    if trouvee is None:
        return None
    return tuple(int(morceau) for morceau in trouvee.group(1).split("."))


def version_de(texte: str | None) -> str | None:
    """La version qu'un texte porte (`--version`, ou ce que la personne a dit), telle quelle."""
    trouvee = _VERSION.search(texte or "")
    return trouvee.group(1) if trouvee is not None else None


@dataclass(frozen=True)
class Client:
    """Un client d'agents que la personne utilise : lequel, à quelle version, et comment on le sait.

    `cle` est celle de `CLIENTS_CONNUS` quand le client y est, sa forme (`_forme`) sinon — un
    client inconnu reste un client, avec le nom que la personne lui a donné (`libelle`).
    `version` vaut `None` quand elle n'a pas pu être lue ni n'a été dite : c'est une inconnue,
    jamais une version supposée. `origine` dit où on l'a trouvé — le chemin résolu sur le poste,
    ou ce que la conversation en a dit.
    """

    cle: str
    libelle: str
    version: str | None = None
    source: str = SOURCE_POSTE
    origine: str = ""

    @property
    def connu(self) -> ClientConnu | None:
        """Ce qu'on sait de ce client, `None` s'il n'est pas dans la table."""
        return client_connu(self.cle)

    def nomme(self) -> str:
        """« Claude Code 2.1.281 (trouvé sur ce poste) » — le nom, la version, et d'où on le tient.

        Sans version, le nom seul : c'est la raison du pont qui dit « version inconnue » quand
        elle décide de quelque chose, et elle ne décide de rien pour un client qui lit
        `AGENTS.md` à toutes ses versions.
        """
        version = f" {self.version}" if self.version else ""
        d_ou = (
            "trouvé sur ce poste" if self.source == SOURCE_POSTE else "nommé dans la conversation"
        )
        return f"{self.libelle}{version} ({d_ou})"


def clients_depuis_texte(valeur: str) -> tuple[Client, ...]:
    """Les clients qu'une valeur comprise nomme — `"Gemini CLI, Claude Code 2.1.200"`.

    La valeur est celle qu'écrit le modèle sous le sujet `clients` du questionnaire : une liste
    **séparée par des virgules** (le prompt le lui demande), chaque élément par son nom et, si la
    personne l'a dite, sa version. Le découpage est structurel ; ce qui reconnaît un client est la
    table (`client_connu`). Un nom qu'elle ne connaît pas est gardé tel quel.
    """
    clients: dict[str, Client] = {}
    for morceau in re.split(r"[,;\n]+", valeur or ""):
        brut = " ".join(morceau.split())
        if not brut:
            continue
        version = version_de(brut)
        nom = _VERSION_ECRITE.sub("", brut).strip(" -—:") if version else brut
        if not nom:
            continue
        connu = client_connu(nom)
        cle = connu.cle if connu is not None else _forme(nom)
        if not cle:
            continue
        clients.setdefault(
            cle,
            Client(
                cle=cle,
                libelle=connu.libelle if connu is not None else nom,
                version=version,
                source=SOURCE_CONVERSATION,
                origine=brut,
            ),
        )
    return tuple(clients.values())


def reunir(poste: Iterable[Client], dits: Iterable[Client]) -> tuple[Client, ...]:
    """Les clients du poste, puis ceux que la conversation ajoute — un par clé.

    Le poste l'emporte quand il a **mesuré** une version : c'est une lecture, là où une version
    dite peut dater. Sans version lue sur le poste, celle qui a été dite la complète.
    """
    reunis: dict[str, Client] = {}
    for client in poste:
        reunis.setdefault(client.cle, client)
    for client in dits:
        deja = reunis.get(client.cle)
        if deja is None:
            reunis[client.cle] = client
        elif deja.version is None and client.version is not None:
            reunis[client.cle] = Client(
                cle=deja.cle,
                libelle=deja.libelle,
                version=client.version,
                source=deja.source,
                origine=deja.origine,
            )
    return tuple(reunis.values())


def noms_en_texte() -> str:
    """Les noms des clients connus, tels que le prompt du questionnaire les cite."""
    return ", ".join(connu.libelle for connu in CLIENTS_CONNUS)


def par_cle(clients: Sequence[Client]) -> dict[str, Client]:
    """`clé → client`, le premier l'emportant."""
    trouves: dict[str, Client] = {}
    for client in clients:
        trouves.setdefault(client.cle, client)
    return trouves
