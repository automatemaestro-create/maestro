"""L'acte qu'un agent s'apprête à commettre : l'outil et ses arguments (#581).

Le chantier #573 déplace le déclencheur de l'arbitrage humain du **texte de la
tâche** vers l'**acte** — développer une fonction de suppression n'est pas
exécuter une suppression. Une fois ce déplacement fait, ce qu'un humain doit voir
pour trancher change avec lui : ni le titre ni la description de la tâche, mais
l'outil appelé et ce qu'on lui passe. « Rédiger le README » n'aide personne à
trancher un `rm -rf`.

Ce module ne porte que la **forme** de ces arguments, et il est **feuille** — il
n'importe rien de `maestro` — pour la raison qui vaut déjà pour
`maestro.references`, `maestro.detail_tache` et `maestro.plan_run` : la forme
traverse des couches qui ne peuvent pas dépendre les unes des autres. Le moteur
la produit (`maestro.engine.guardrails.DemandeValidation`), la Control Tower la
publie (`maestro.controltower.validation.evenement_demande`), l'événement la
transporte et la projection la sert à l'écran.

Deux décisions y tiennent :

- les arguments voyagent en **texte, clé par clé** (`dict[str, str]`) et non en
  JSON arbitraire. Ce qui entre est le `tool_input` du SDK, un objet dont les
  clés dépendent de l'outil et dont les valeurs peuvent être n'importe quoi — y
  compris ce qui ne se sérialise pas. Le rendre en chaînes ici, une fois, garantit
  que rien de ce qui part sur le bus ne peut faire échouer une publication, et
  laisse à l'écran une structure à rendre plutôt qu'un blob à découper ;
- chaque valeur est **bornée** (`ARGUMENT_MAX`). Un `content` de `Write` ou un
  `prompt` n'a aucune longueur naturelle : sans borne, une demande d'arbitrage
  publierait un fichier entier sur le bus, dans la projection et jusqu'au
  WebSocket. Le **nombre** de clés, lui, n'est pas borné — il est celui du schéma
  de l'outil, et rien n'en produit mille.

Ce qui n'est **pas** ici : l'expurgation des secrets. Elle vit là où vivent déjà
celles du titre et de la description — au moment de publier
(`evenement_demande`) —, pour qu'il n'y ait qu'un seul endroit où lire ce qui est
expurgé avant de sortir.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Longueur maximale d'une **valeur** d'argument conservée. Plus généreux que la
#: cible d'une ligne d'activité (`maestro.providers.activite.CIBLE_MAX`, 120) et
#: pour une raison de nature : une ligne d'activité se lit d'un œil pendant que
#: ça tourne, un argument se lit pour **décider**, et une commande shell tronquée
#: au milieu ferait approuver autre chose que ce qu'on croit lire. Assez long
#: pour une commande ou un patch court, assez court pour qu'un contenu de fichier
#: ne noie ni le bus ni l'écran.
ARGUMENT_MAX = 1000


def arguments_depuis(donnees: Any) -> dict[str, str]:
    """Les arguments lisibles d'une valeur brute — **dict vide** quand il n'y en a pas.

    Même régime que les autres relectures du flux (`etapes_depuis`,
    `sources_depuis`, `reponses_depuis`) : **relecture, jamais nouvelle saisie**.
    Ce qui arrive vient soit du SDK (le `tool_input` d'un appel d'outil), soit du
    bus après un aller-retour JSON, soit du rejeu d'un journal durable — trois
    producteurs dont aucun n'a à faire échouer une demande d'arbitrage parce
    qu'une valeur n'avait pas la forme attendue.

    D'où la tolérance, entrée par entrée : ce qui n'est pas un objet rend `{}`,
    une clé qui n'est pas une chaîne est écartée sans faire perdre les autres, et
    une valeur qui n'est pas une chaîne est **rendue en texte** plutôt que jetée —
    un `timeout: 120` ou un `recursive: true` fait partie de ce qu'on arbitre.
    """
    if not isinstance(donnees, Mapping):
        return {}
    arguments: dict[str, str] = {}
    for cle, valeur in donnees.items():
        if not isinstance(cle, str) or not cle:
            continue
        arguments[cle] = _borne(valeur if isinstance(valeur, str) else repr(valeur))
    return arguments


def _borne(texte: str) -> str:
    """Ramène `texte` à `ARGUMENT_MAX` caractères, **en disant qu'il a été coupé**.

    Les sauts de ligne sont gardés, contrairement à la cible d'une ligne
    d'activité qui les écrase : un script passé à `Bash` ou un contenu de fichier
    se lisent sur plusieurs lignes, et les aplatir rendrait illisible ce qu'on
    demande d'approuver.
    """
    if len(texte) <= ARGUMENT_MAX:
        return texte
    return texte[:ARGUMENT_MAX].rstrip() + "…"
