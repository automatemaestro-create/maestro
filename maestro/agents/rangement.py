"""Le rangement **par projet** de la configuration d'un agent (ticket #1038).

Un agent était une ressource du **poste** : sa définition, son playbook, ses
autorisations, ses serveurs MCP et sa capacité valaient pour tous les projets à
la fois (`core/agents/`, `core/playbooks/`, `core/permissions/`, `core/mcp/`,
`core/capacite/`). [docs/37 §2.1](../../docs/37-decision-equipe-sur-mesure.md)
renverse cette décision : **un agent appartient à un projet**, et une équipe
dérivée d'un projet ne porte pas les skills ni les autorisations d'un autre.

Ce module porte la règle de rangement, **écrite une seule fois** pour les six
dépôts qui la partagent. Sans lui, la même décision vivrait en six exemplaires
et divergerait au premier dépôt qu'on oublie.

## Deux niveaux, et un seul sens de lecture

- **Le gabarit** — la racine de chaque dépôt (`core/agents/`, …). C'est ce qui
  existait avant, et c'est ce que #1039 consultera pour proposer une équipe :
  un catalogue de rôles, jamais instancié d'office (#1042) — ni les fiches
  rangées là, ni les cinq gabarits que le code livre.
- **Le projet** — `<racine>/_projets/<projet_id>/`, la même arborescence, un
  cran plus bas. C'est là que vit ce qu'un projet a réglé pour lui.

Le tiret bas de `_projets` n'est pas décoratif : un nom d'agent commence par
``[a-z0-9]`` dans les six dépôts, donc **aucun agent ne peut porter ce nom** et
le segment ne peut jamais être pris pour un agent. C'est ce qui évite d'avoir à
réserver un mot dans six listes différentes.

## La règle : le projet recouvre le gabarit, fichier par fichier

Ce qu'un projet **règle** lui appartient ; ce qu'il ne dit pas, il en **hérite**.
Un playbook édité hier, une politique d'autorisations, un agent désactivé, une
déclaration MCP continuent donc de valoir tant que le projet n'a rien posé
par-dessus — sinon ce lot ferait disparaître en silence, sur chaque installation
existante, la configuration accumulée jusqu'ici. Pour les autorisations, le
silence vaudrait même « tout permis » : *un garde-fou qui saute est pire qu'un
garde-fou absent*.

**Une exception, nommée** : l'**existence** d'un agent ne s'hérite pas
(`AgentStore.herite_du_gabarit = False`). Un réglage a un défaut sensé, pas
l'appartenance : un agent personnalisé rangé au niveau gabarit est un *gabarit*,
pas un membre de l'équipe d'un projet — ce que #1042 a achevé en retirant aussi
les fiches du code du catalogue effectif : un projet naît sans agent, et son
catalogue est vide tant que son équipe n'a pas été validée. La **reprise**
(`maestro.agents.reprise`) est ce qui rattache au projet ceux qui y travaillaient
déjà, sans rien supprimer.

## Ce que ce module ne décide pas

- **Qui** est le projet actif : c'est le paramètre `?projet=` de l'API
  (`maestro.controltower.portee.resoudre_projet_configuration`) et le `projet_id`
  d'une tâche à l'exécution.
- **Le routage** sur l'équipe d'un projet — quel agent prend quelle tâche —,
  qui est le lot #1041.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Self

from maestro.appartenance import projet_id_valide

#: Le segment qui sépare les gabarits (la racine) des configurations de projet.
#: Le tiret bas le met **hors d'atteinte d'un nom d'agent**, qui commence par
#: ``[a-z0-9]`` dans les six dépôts : aucune réservation à tenir à jour.
SEGMENT_PROJETS = "_projets"


def racine_du_projet(racine: Path, projet_id: str) -> Path:
    """`<racine>/_projets/<projet_id>` — le rangement par projet, écrit ici et nulle part ailleurs.

    Lève `ValueError` si `projet_id` n'est pas un identifiant de projet
    recevable (`maestro.appartenance.projet_id_valide`) : un identifiant venu de
    l'extérieur ne doit jamais pouvoir sortir de la racine du dépôt — c'est la
    même garde que le `_NOM_AGENT` des six dépôts sur un nom d'agent.
    """
    identifiant = projet_id_valide(projet_id)
    if identifiant is None:
        raise ValueError(
            f"identifiant de projet invalide : {projet_id!r} "
            "(slug [a-z0-9_-] de 64 caractères au plus attendu)."
        )
    return racine / SEGMENT_PROJETS / identifiant


class RangeParProjet:
    """Un dépôt de configuration d'agent qui sait se cadrer sur un projet (#1038).

    Le socle commun des six dépôts (`AgentStore`, `SurchargeStore`,
    `PlaybookStore`, `PermissionStore`, `McpStore`, `CapacityStore`) : tous ont
    une racine et rien d'autre à la construction, tous se dérivent de la même
    façon. Il leur donne `racine`, `gabarits` et `pour_projet`, et **eux** posent
    le repli là où il a un sens — une absence de fichier n'est pas la même chose
    dans un dépôt de définitions et dans un dépôt de réglages (voir l'en-tête du
    module).

    Un dépôt construit nu **est** celui des gabarits : `gabarits` vaut None, et
    `pour_projet(None)` le rend tel quel — une API sans projet actif, un moteur
    sans projet de tâche, et les tests d'avant ce lot lisent donc exactement ce
    qu'ils lisaient.
    """

    #: Ce dépôt hérite-t-il du gabarit ce que le projet ne dit pas ? Vrai pour
    #: les **réglages** (playbook, autorisations, MCP, capacité, surcharges),
    #: faux pour ce qui décide de l'**existence** d'un agent (`AgentStore`).
    herite_du_gabarit: ClassVar[bool] = True

    def __init__(self, racine: Path, gabarits: Self | None = None) -> None:
        self._racine = racine
        self._gabarits = gabarits

    @property
    def racine(self) -> Path:
        """La racine du dépôt — celle des gabarits, ou celle d'un projet."""
        return self._racine

    @property
    def gabarits(self) -> Self | None:
        """Le dépôt de repli, ou None quand ce dépôt **est** celui des gabarits.

        C'est aussi la réponse à « ce dépôt est-il cadré sur un projet ? » pour
        les cinq dépôts de réglages. `AgentStore`, qui n'hérite pas, garde None
        des deux côtés : demander l'appartenance à un dépôt se fait par
        `racine`, jamais par l'absence de repli.
        """
        return self._gabarits

    def pour_projet(self, projet_id: str | None) -> Self:
        """Le même dépôt, cadré sur `projet_id` — ou celui-ci si `projet_id` est None.

        `None` rend `self` **intact** (et non une copie) : c'est le niveau des
        gabarits, qui est aussi ce que lisait tout le code d'avant ce lot. Un
        identifiant invalide lève `ValueError` (`racine_du_projet`).
        """
        if projet_id is None:
            return self
        gabarits = self if type(self).herite_du_gabarit else None
        return type(self)(racine_du_projet(self._racine, projet_id), gabarits)
