"""L'**espace de données** d'une stack : chaque copie de travail voit les siennes (#1164).

    from maestro.espace import espace_courant, nom_redis

    espace_courant()                        # Espace(nom='1164-une-stack-…', origine='worktree')
    nom_redis("maestro.evenements")         # '1164-une-stack-…:maestro.evenements'

Chaque copie de travail avait déjà ses ports (`worktree.sh ensure`, #152) et ses
dépôts de fichiers — fils, projets, équipes vivent sous `core/` **de la copie**
(`ChatStore.default`, `ProjetStore.default`…, résolus depuis l'emplacement du
code). Il lui manquait la moitié Redis : toutes les stacks réelles du poste
parlaient à la même instance (`REDIS_URL`) sous les mêmes noms, si bien qu'une
relecture lancée depuis un worktree rejouait au démarrage le journal d'un autre,
et recevait en direct les événements de ses runs.

## Un espace, un préfixe

Un **espace** est un nom. Chaque clé et chaque canal Redis de Maestro s'y range
par un préfixe, `<espace>:<nom>` — le journal durable, les battements, la file de
tâches, le canal des événements, les boîtes aux lettres. Le préfixe vaut pour les
canaux **Pub/Sub** aussi, et c'est ce qui écarte l'autre piste : la base Redis
numérotée (`redis://…/3`) sépare les clés mais **pas** les canaux, qu'une instance
partage entre toutes ses bases. Deux stacks sur deux bases recevraient toujours
les événements l'une de l'autre.

## D'où vient le nom

Dans l'ordre, et la première réponse gagne :

1. **`MAESTRO_ESPACE`** — posé par `scripts/controltower/start.sh`, qui l'exporte
   à l'API, donc aux hôtes détachés qu'elle lance (ils héritent de son
   environnement) : une stack démarrée n'a qu'un espace, quel que soit le
   répertoire courant de ses process.
2. **Le worktree de la copie** — son nom git, lu dans le fichier `.git` qu'un
   worktree porte à la place du dossier (`gitdir: …/.git/worktrees/<nom>`). Git
   garantit ce nom unique parmi les worktrees d'un dépôt. La copie est celle **du
   code qui tourne** (`racine_de_la_copie`), comme pour les dépôts de `core/` :
   un `python -m maestro.controltower.purge` joué dans un worktree vise les
   données de ce worktree, jamais celles du clone principal.
3. **Le clone principal** — l'espace `commun`, **sans préfixe** : les noms
   d'avant ce ticket, donc l'historique du poste intact.

Le nom se résout **à la construction** de chaque objet Redis (bus, journal,
registre, boîtes, file), jamais à l'import : c'est ce qui permet à un test de
construire deux stacks dans le même process, et à la suite de figer l'espace
une fois pour toutes dans `tests/conftest.py` — le verdict ne dépend pas de la
copie où l'on joue la suite.

⚠ `commun` est un nom **réservé** : c'est l'espace sans préfixe. Un worktree qui
s'appellerait ainsi reçoit `copie-commun`, pour ne jamais lire le clone
principal par homonymie.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: La variable qui fixe l'espace d'une stack — posée par `start.sh`, héritée ensuite.
VARIABLE_ESPACE = "MAESTRO_ESPACE"

#: L'espace du clone principal, **sans préfixe** : les noms Redis d'avant #1164.
COMMUN = "commun"

#: D'où vient le nom — dit à l'annonce du démarrage, pour qu'on sache s'il a été
#: choisi ou déduit.
ORIGINE_VARIABLE = VARIABLE_ESPACE
ORIGINE_WORKTREE = "worktree"
ORIGINE_CLONE = "clone principal"

#: Ce qu'un nom d'espace admet : il finit dans des clés Redis et dans des chemins,
#: donc ni séparateur (`:`), ni espace, ni joker de `SCAN` (`*`, `?`, `[`).
_NOM_VALIDE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HORS_NOM = re.compile(r"[^A-Za-z0-9._-]+")


class EspaceInvalide(ValueError):
    """`MAESTRO_ESPACE` porte un nom qu'aucune clé Redis ne doit recevoir."""


@dataclass(frozen=True)
class Espace:
    """Un espace de données : son nom, et d'où il vient."""

    nom: str
    origine: str

    @property
    def commun(self) -> bool:
        """Vrai pour l'espace du clone principal — celui qui ne préfixe rien."""
        return self.nom == COMMUN

    @property
    def prefixe(self) -> str:
        """Ce qui précède chaque nom Redis de cet espace (`""` pour l'espace commun)."""
        return "" if self.commun else f"{self.nom}:"

    def nommer(self, nom: str) -> str:
        """Le nom Redis `nom` rangé dans cet espace — `nom` lui-même dans l'espace commun."""
        return nom if self.commun else f"{self.nom}:{nom}"


def racine_de_la_copie() -> Path:
    """La copie de travail dont le code tourne — la même que celle des dépôts de `core/`."""
    return Path(__file__).resolve().parents[1]


def nom_de_worktree(racine: Path) -> str | None:
    """Le nom git du worktree que `racine` est, ou None pour un clone (ou rien de lisible).

    Un worktree porte un **fichier** `.git` (`gitdir: <commun>/worktrees/<nom>`)
    là où un clone a un dossier. On le lit sans lancer git : ce nom se résout à la
    construction de chaque objet Redis, et un sous-process par construction serait
    un prix sans raison.
    """
    point_git = racine / ".git"
    if not point_git.is_file():
        return None
    try:
        contenu = point_git.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for ligne in contenu.splitlines():
        cle, _, valeur = ligne.partition(":")
        if cle.strip() == "gitdir" and valeur.strip():
            # Un chemin Windows (`E:/…` ou `E:\…`) se lit aussi depuis le conteneur
            # Linux du filet CI : on n'en garde que le dernier segment.
            return PurePosixPath(valeur.strip().replace("\\", "/")).name or None
    return None


def _assaini(nom: str) -> str:
    """Un nom de worktree rendu recevable — jamais `commun`, jamais vide."""
    propre = _HORS_NOM.sub("-", nom).strip("-.")[:128] or "copie"
    if not propre[0].isalnum():
        propre = f"copie-{propre}"[:128]
    return f"copie-{propre}" if propre == COMMUN else propre


def espace_courant(
    environnement: Mapping[str, str] | None = None, *, racine: Path | None = None
) -> Espace:
    """L'espace de la stack : `MAESTRO_ESPACE`, sinon le worktree de la copie, sinon `commun`.

    Lève `EspaceInvalide` sur un `MAESTRO_ESPACE` irrecevable : un nom choisi
    qu'on corrigerait en silence désignerait un autre espace que celui qu'on a
    demandé — et c'est précisément le partage qu'on veut rendre impossible.
    """
    env = os.environ if environnement is None else environnement
    regle = (env.get(VARIABLE_ESPACE) or "").strip()
    if regle:
        if not _NOM_VALIDE.match(regle):
            raise EspaceInvalide(
                f"{VARIABLE_ESPACE}={regle!r} n'est pas un nom d'espace recevable "
                "(lettres, chiffres, « . », « _ », « - », 128 caractères au plus)."
            )
        return Espace(regle, ORIGINE_VARIABLE)
    worktree = nom_de_worktree(racine or racine_de_la_copie())
    if worktree is None:
        return Espace(COMMUN, ORIGINE_CLONE)
    return Espace(_assaini(worktree), ORIGINE_WORKTREE)


def nom_redis(nom: str, environnement: Mapping[str, str] | None = None) -> str:
    """Le nom Redis `nom` rangé dans l'espace courant — le geste de chaque objet Redis."""
    return espace_courant(environnement).nommer(nom)
