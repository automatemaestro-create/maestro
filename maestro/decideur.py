"""Qui tranche un acte soumis à arbitrage : `auto`, `orchestrateur` ou `humain` (#586).

Le chantier #573 a déplacé le **déclencheur** de l'arbitrage du texte de la tâche
vers l'acte (#585), puis armé sa **suspension** au vol (#583). Restait la question
que ni l'un ni l'autre ne posait : une fois l'acte intercepté, *qui* tranche ?

Tout ne mérite pas de réveiller une personne. Certains actes se laissent passer en
se contentant d'être vus, d'autres se tranchent par l'orchestrateur, d'autres
n'appartiennent qu'à un humain. Mais « et si l'orchestrateur répondait lui-même ? »
ne peut pas être une décision de LLM prise au vol : elle ne serait ni traçable ni
testable, et le garde-fou reviendrait à faire garder un LLM par un LLM. Le cran est
donc **posé dans la politique** (`maestro.agents.permissions`), à froid, versionné
avec le dépôt — et l'orchestrateur n'applique que celui qu'on lui donne.

Trois crans, du plus ouvert au plus fermé :

- **`auto`** — personne n'est sollicité. L'appel passe, et il est **tracé** : c'est
  toute la différence avec un `allow`, qui passe en silence. Le cran de ce qu'on
  veut voir sans vouloir l'arrêter. N'ayant besoin d'aucun décideur, il ne dépend
  d'**aucun canal** : un appelant qui exécute hors de la Control Tower le rend
  comme les autres ;
- **`orchestrateur`** — la machine tranche, seule, sans humain. Elle peut refuser ;
  elle peut approuver **ce cran-ci**, et rien d'autre ;
- **`humain`** — une personne, et personne d'autre. C'est le **défaut**, et le
  défaut n'est pas un détail de mise en œuvre : *un cran non précisé escalade, il
  ne s'auto-approuve pas*. Une politique écrite avant ce lot, une valeur qu'on ne
  sait pas relire, un producteur qui n'en dit rien — tous retombent ici.

## L'asymétrie, et pourquoi elle vit dans le routage plutôt que dans une politesse

EF-08/ENF-04 : **refuser est le défaut sûr, approuver ne l'est jamais**. La
conséquence tient en une phrase — *l'orchestrateur ne peut jamais approuver un
acte classé `humain`* — et elle n'est pas tenue par une vérification qu'on aurait
pu oublier d'écrire : sur le cran `humain`, le canal de l'orchestrateur n'est
**pas sur le chemin** (`maestro.engine.guardrails.Guardrails.demande_validation`).
Il ne se voit pas refuser une approbation, il n'est pas consulté du tout. Un
garde-fou qu'on ne peut pas contourner est un garde-fou qui n'a rien à refuser.

Ce que l'orchestrateur peut, en revanche, il le peut **seul** : refuser, et
répondre à une demande d'information — le canal `brief.questions` /
`brief.reponses` (#321), dont il est déjà l'acteur (`ACTEUR_ORCHESTRATEUR`, repris
par `maestro.controltower.brief`). Répondre à une question n'est pas approuver un
acte, et c'est pour cela que ce pouvoir-là ne lui est pas retiré.

## Un module feuille, à la racine

Comme `maestro.acte` (#581) et `maestro.deliberation` (#584), et pour la même
raison : le cran est lu des deux côtés de plusieurs frontières — la **politique**
le pose par entrée, le **garde-fou** le route, le **fournisseur** court-circuite
`auto` sans canal, la **Control Tower** l'affiche et le journal le consigne. Le
ranger sous l'un des quatre obligerait les trois autres à en dépendre.
"""

from __future__ import annotations

from enum import StrEnum


class Decideur(StrEnum):
    """Qui tranche un acte soumis à arbitrage — trois crans, `HUMAIN` par défaut.

    Valeurs en clair : elles voyagent telles quelles en JSON (fichier de
    politique, journal, API, fil temps réel) et se lisent sans table de
    correspondance. C'est le même parti pris que `Verdict` (#580), avec lequel
    elles ne se confondent pas : le verdict dit **ce qui arrive à l'appel**
    (passe / arbitrage / refus), le décideur dit **qui tranche** quand il est
    arbitré.
    """

    AUTO = "auto"
    ORCHESTRATEUR = "orchestrateur"
    HUMAIN = "humain"


#: Le cran d'un acte dont personne n'a précisé qui le tranche. `HUMAIN`, et c'est
#: le critère du ticket : *un cran non précisé escalade, il ne s'auto-approuve
#: pas*. Un défaut à `AUTO` ferait d'un oubli de politique un laissez-passer, ce
#: qui est exactement le défaut symétrique de celui que le parent #573 répare.
DECIDEUR_DEFAUT = Decideur.HUMAIN

#: Le nom sous lequel l'orchestrateur signe ce qu'il décide et ce qu'il demande.
#: Une seule orthographe pour les deux canaux qu'il tient — l'arbitrage d'un acte
#: classé `orchestrateur` et les allers-retours de clarification du brief (#321,
#: `maestro.controltower.brief.ACTEUR_BRIEF`) — parce que deux orthographes
#: rendraient illisible la trace de ce qu'un même acteur a fait.
ACTEUR_ORCHESTRATEUR = str(Decideur.ORCHESTRATEUR)


def decideur_depuis(brut: object) -> Decideur:
    """Relit un cran venu du dehors — **`HUMAIN` dès que la lecture échoue**.

    Régime de **relecture**, jamais de saisie : ce qui arrive ici vient d'un
    événement du bus, d'une projection ou du rejeu d'un journal durable — trois
    producteurs dont aucun n'a à faire échouer une demande d'arbitrage parce
    qu'une valeur a vieilli. Même parti pris que `arguments_depuis`
    (`maestro.acte`) et `etapes_depuis` : on relit ce qu'on reconnaît, on retombe
    sur le défaut pour le reste.

    Et le défaut, ici, est le seul qui soit sûr. Une valeur inconnue — un cran
    ajouté par une version plus récente, une chaîne tronquée — escalade vers
    l'humain ; elle ne s'auto-approuve pas. C'est le pendant exact de la règle
    d'écriture, qui elle **refuse** franchement : une politique qu'on est en
    train de charger peut encore être corrigée, un événement déjà émis non.
    """
    try:
        return Decideur(str(brut))
    except ValueError:
        return DECIDEUR_DEFAUT
