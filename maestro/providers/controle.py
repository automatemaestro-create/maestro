"""Le point de contrôle des appels d'outil **ferme par défaut** (ticket #1304).

Le point de contrôle est l'endroit où Maestro applique ses garde-fous aux outils
d'un agent : avant chaque appel, il le nomme, le confronte à la frontière du
projet (`maestro.sandbox.en_place`), à la politique de l'agent
(`maestro.agents.permissions`) et à la portée de ses crans (`maestro.portee`).
Chez le fournisseur Claude, c'est le hook `PreToolUse` que le SDK sert au CLI
(`maestro.providers.claude._hook_permissions`).

Tout ce qu'il décide repose sur **ce qu'il lit de l'appel** : un nom, une entrée.
Jusqu'à ce ticket, un appel sans nom lisible passait — `{}`, c'est-à-dire
« laisser passer » —, et un outil de fichiers sans son argument de chemin aussi.
Une montée de version du CLI qui déplacerait ces champs ouvrait donc tout, **sans
un mot**. Ce module porte la règle inverse et ses deux verbes :

- `nom_outil` et `entree_outil` lisent l'appel **ou rendent `None`** — jamais une
  valeur devinée ;
- ce qu'on ne sait pas lire est **refusé avec un motif** (`motif_sans_nom`,
  `motif_entree_illisible`), servi à l'agent et tracé au journal comme n'importe
  quel refus.

C'est l'asymétrie d'EF-08/ENF-04, la même que celle d'un cran d'arbitrage sans
canal : ce qu'on ne sait pas juger ne passe pas. Le prix est nommé — un CLI qui
changerait son contrat verrait tous ses appels refusés — et il est voulu : un
garde-fou qui ne tient plus est pire qu'un garde-fou absent, et c'est la sonde de
démarrage (plus bas) qui le dit **avant** qu'un agent ne s'y heurte.

La sonde de démarrage
---------------------

Fermer par défaut ne dit pas qu'un refus **tient**. Le point de contrôle rend un
verdict ; c'est le fournisseur qui l'applique, et rien, dans le code de Maestro,
ne prouvait qu'il le faisait encore : les tests jouaient des doubles. La sonde
est cette preuve, rejouée sur le fournisseur réel au démarrage d'une session
d'agent :

1. une session courte, montée **comme celle de l'agent** (même CLI, même
   environnement, même mode de permissions), porte un seul outil,
   `NOM_OUTIL_SONDE`, sur un serveur à elle (`NOM_SERVEUR_SONDE`) ;
2. le point de contrôle de cette session applique la **vraie** politique de
   Maestro, qui refuse cet outil (`POLITIQUE_SONDE`) ;
3. on demande à l'agent de la sonde de l'appeler une fois, et un **témoin**
   (`TemoinSonde`) relève ce qui s'est passé : les noms que le point de contrôle
   a lus, et si l'outil a été exécuté.

Le verdict se lit sur le témoin, **jamais dans la prose** de l'agent de la
sonde : ce qu'il répond n'est pas lu. Trois issues :

- l'outil n'a pas été exécuté et le point de contrôle a lu son nom : le refus
  **tient** ;
- l'outil a été exécuté, ou le point de contrôle n'a pas su le nommer : le
  garde-fou est **inopérant** (`GardeFouInoperant`), la tâche ne démarre pas —
  c'est une propriété du fournisseur, qu'une relance ne changerait pas ;
- l'outil n'a pas été appelé : la sonde est **non concluante**
  (`SondeNonConcluante`), la tâche ne démarre pas non plus, et la relance du
  moteur (ENF-06) la rejoue — rien n'a été prouvé, ni dans un sens ni dans
  l'autre.

Ce que la sonde **ne prouve pas** est nommé : elle éprouve le mécanisme — le
fournisseur consulte-t-il le point de contrôle, lui en passe-t-il le nom, et
applique-t-il son refus ? —, pas chaque règle d'une politique ni la frontière
d'un projet, qui ont leurs tests. Elle appelle un vrai modèle : c'est le seul
moyen de faire naître un appel d'outil chez un fournisseur réel, et c'est aussi
pourquoi son verdict se garde le temps du fournisseur qui l'a rendu
(`maestro.providers.claude.ClaudeProvider`).

Le module est **feuille** — il n'importe rien de `maestro` hors de
`maestro.providers.base` — pour la raison qui vaut déjà pour `maestro.lecture` :
ce qu'il dit se lit par le hook du fournisseur, et doit s'éprouver sans monter ni
session, ni politique, ni projet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maestro.providers.base import GardeFouInoperant, SondeNonConcluante

#: Ce que la trace d'un refus nomme quand l'appel n'a pas de nom lisible. Entre
#: parenthèses parce qu'aucun outil ne s'appelle ainsi : une politique ne peut pas
#: le citer, et la ligne de journal ne fait croire à aucun outil réel.
OUTIL_SANS_NOM = "(outil sans nom)"

#: Le serveur MCP de la sonde — le **sien**, distinct du serveur `maestro` des
#: verbes de l'agent : la politique de la sonde le refuse en entier, et un verbe
#: de Maestro n'a pas à tomber sous ce refus. Le nom ne partage pas de préfixe à
#: frontière `__` avec `maestro` (`mcp__maestro` ne couvre pas `mcp__maestro_sonde`).
NOM_SERVEUR_SONDE = "maestro_sonde"

#: L'outil que la sonde fait appeler, et que sa politique refuse.
NOM_OUTIL_SONDE = "sonder"

#: La description de l'outil, lue par l'agent de la sonde.
DESCRIPTION_OUTIL_SONDE = (
    "Sonde de démarrage de Maestro : appelle cet outil une fois, sans argument, "
    "quand on te le demande. Il peut être refusé, et c'est attendu."
)

#: La consigne système de la session de sonde : un seul geste, rien d'autre.
CONSIGNE_SONDE = (
    "Tu es la sonde de démarrage de Maestro. Tu as un seul geste à faire : appeler "
    f"l'outil `{NOM_OUTIL_SONDE}` une fois, sans argument. Qu'il réussisse ou qu'il "
    "soit refusé, ne le rappelle pas, et termine par le seul mot FIN."
)

#: Le message de la session de sonde.
PROMPT_SONDE = f"Appelle maintenant l'outil `{NOM_OUTIL_SONDE}`, une seule fois, sans argument."

#: Ce que l'outil rend s'il est exécuté — ce qui ne devrait jamais arriver.
REPONSE_OUTIL_SONDE = "exécuté"


def nom_outil(appel: object) -> str | None:
    """Le nom de l'outil appelé, tel que le point de contrôle le lit — `None` s'il ne se lit pas.

    Un nom se lit s'il est une chaîne **non vide** : une valeur d'un autre type
    ou une chaîne blanche ne nomment rien, et le point de contrôle ne devine pas.
    """
    if not isinstance(appel, Mapping):
        return None
    brut = appel.get("tool_name")
    if not isinstance(brut, str) or not brut.strip():
        return None
    return brut


def entree_outil(appel: object) -> Mapping[str, Any] | None:
    """L'entrée de l'appel — `None` si elle n'est pas un objet (absente comprise).

    Tout outil prend un objet, fût-il vide (`{}`) : une entrée qui n'en est pas
    un n'est pas un appel sans argument, c'est un appel qu'on ne sait pas lire.
    """
    if not isinstance(appel, Mapping):
        return None
    brut = appel.get("tool_input")
    return brut if isinstance(brut, Mapping) else None


def motif_sans_nom() -> str:
    """Le motif d'un appel refusé faute de nom lisible — servi à l'agent et tracé."""
    return (
        "Appel d'outil refusé : le point de contrôle de Maestro n'en lit pas le nom. "
        "Ce qu'il ne sait pas nommer, il ne le laisse pas passer — ni la politique, "
        "ni la frontière du projet, ni l'arbitrage ne pourraient s'y appliquer."
    )


def motif_entree_illisible(outil: str) -> str:
    """Le motif d'un appel refusé parce que son entrée n'est pas un objet lisible."""
    return (
        f"Appel de `{outil}` refusé : son entrée n'est pas lisible par le point de "
        "contrôle de Maestro (ce n'est pas un objet). Ce qu'il ne sait pas lire, il "
        "ne le laisse pas passer."
    )


# --- La sonde de démarrage ---------------------------------------------------------------


def nom_complet_sonde() -> str:
    """Le nom sous lequel le point de contrôle doit lire l'outil de la sonde.

    La forme `mcp__<serveur>__<outil>` est celle que la politique de Maestro
    reconnaît pour un outil MCP (`maestro.agents.permissions`) : c'est elle que la
    sonde doit voir revenir. Un fournisseur qui nommerait autrement l'outil ne
    tomberait pas sous le refus — et la sonde le dirait, parce que l'outil serait
    alors exécuté.
    """
    return f"mcp__{NOM_SERVEUR_SONDE}__{NOM_OUTIL_SONDE}"


#: La politique que la sonde applique : le serveur de la sonde refusé en entier.
#: Une **forme** de politique et non une `PolitiqueOutils` — ce module est feuille ;
#: l'adaptateur la construit (`maestro.providers.claude`).
POLITIQUE_SONDE: Mapping[str, Sequence[str]] = {"deny": (f"mcp__{NOM_SERVEUR_SONDE}",)}


@dataclass
class TemoinSonde:
    """Ce que la session de sonde a laissé voir — la seule matière du verdict.

    `vus` sont les appels tels que le point de contrôle les a lus, dans l'ordre :
    leur nom, ou `OUTIL_SANS_NOM`. `executions` compte les exécutions de l'outil
    de la sonde : il est refusé, donc toute exécution est un refus qui n'a pas
    tenu.
    """

    vus: list[str] = field(default_factory=list)
    executions: int = 0

    def voit(self, appel: object) -> None:
        """Le point de contrôle vient d'être consulté sur `appel`."""
        self.vus.append(nom_outil(appel) or OUTIL_SANS_NOM)

    def execute(self) -> str:
        """L'outil de la sonde vient d'être exécuté — rend ce qu'il répond à l'agent."""
        self.executions += 1
        return REPONSE_OUTIL_SONDE

    def verdict(self) -> None:
        """Lève si le refus n'a pas tenu, ou si rien n'a été prouvé ; ne rend rien s'il tient."""
        attendu = nom_complet_sonde()
        lus = ", ".join(f"« {nom} »" for nom in self.vus) or "aucun appel"
        if self.executions:
            raise GardeFouInoperant(
                "Garde-fou inopérant : le fournisseur a exécuté l'outil de la sonde "
                f"(`{attendu}`) que Maestro refusait — le point de contrôle a lu : {lus}. "
                "Ni les refus, ni les arbitrages, ni la frontière du projet ne "
                "tiendraient dans cette session : la tâche ne démarre pas. La cause est "
                "chez le fournisseur (son CLI ou son SDK), pas dans la tâche : une "
                "relance n'y changerait rien."
            )
        if not self.vus:
            raise SondeNonConcluante(
                "Sonde du point de contrôle non concluante : l'agent de la sonde n'a "
                f"pas appelé l'outil `{attendu}`, donc rien ne prouve qu'un refus de "
                "Maestro tient chez ce fournisseur. La tâche ne démarre pas sans cette "
                "preuve."
            )
        if attendu not in self.vus:
            raise GardeFouInoperant(
                "Garde-fou inopérant : le point de contrôle a été consulté sans lire "
                f"le nom de l'outil de la sonde (`{attendu}`) — il a lu : {lus}. Tous "
                "les appels de l'agent seraient refusés sans qu'aucune règle nommée ne "
                "s'y applique : la tâche ne démarre pas. La cause est chez le "
                "fournisseur (son CLI ou son SDK) : une relance n'y changerait rien."
            )
