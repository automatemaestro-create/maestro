"""Qui tranche un acte soumis à arbitrage : `auto` ou `humain` (#586, #715).

Le chantier #573 a déplacé le **déclencheur** de l'arbitrage du texte de la tâche
vers l'acte (#585), puis armé sa **suspension** au vol (#583). Restait la question
que ni l'un ni l'autre ne posait : une fois l'acte intercepté, *qui* tranche ?

Tout ne mérite pas de réveiller une personne : certains actes se laissent passer
en se contentant d'être vus, d'autres n'appartiennent qu'à un humain. Le cran est
**posé dans la politique** (`maestro.agents.permissions`), à froid, versionné avec
le dépôt — jamais déduit au vol.

Deux crans, du plus ouvert au plus fermé :

- **`auto`** — personne n'est sollicité. L'appel passe, et il est **tracé** : c'est
  toute la différence avec un `allow`, qui passe en silence. Le cran de ce qu'on
  veut voir sans vouloir l'arrêter. N'ayant besoin d'aucun décideur, il ne dépend
  d'**aucun canal** : un appelant qui exécute hors de la Control Tower le rend
  comme les autres ;
- **`humain`** — une personne, et personne d'autre. C'est le **défaut**, et le
  défaut n'est pas un détail de mise en œuvre : *un cran non précisé escalade, il
  ne s'auto-approuve pas*. Une politique écrite avant ce lot, une valeur qu'on ne
  sait pas relire, un producteur qui n'en dit rien — tous retombent ici.

⚠ `auto` n'est pas « la machine approuve », et les deux crans ne se lisent bien
qu'ensemble : `auto` est une décision **humaine différée** — prise à froid, par
écrit, versionnée. Le lire comme une décision machine défait ce que ce module
existe pour tenir.

## Il y en avait trois, et le cran du milieu est parti (#715)

Un cran `orchestrateur` a existé entre les deux — *la machine tranche, seule* —,
retiré par la décision de cadrage #647 ([docs/31](../docs/31-decision-cran-orchestrateur.md))
sur trois faits mesurés : il avait une **population de zéro** (aucune entrée `ask`
dans le dépôt), **aucun canal de production** ne le servait (les cinq sites qui
montent un `Guardrails` ne passent que plafonds, délai et `validateur`), et il
recouvrait deux choses dont aucune n'a besoin de lui — ce qui se décide à froid
*est* `auto`, ce qui se décide au jugement est un LLM qui garde un LLM. Un acte
qui lui était classé rendait donc invariablement un refus : *la politique promettait
une décision et rendait un refus*.

⚠ Le cran du milieu n'a pas « fondu » dans `auto`, qui n'hérite de rien : les actes
qui lui revenaient remontent au **défaut**, `humain`. Confondre les deux ferait
d'un retrait un laissez-passer.

## L'asymétrie écriture/relecture — et pourquoi elle tient le retrait toute seule

EF-08/ENF-04 : **refuser est le défaut sûr, approuver ne l'est jamais**. D'où deux
régimes qu'il ne faut surtout pas aligner l'un sur l'autre :

- **en écriture**, une politique qui dit `"orchestrateur"` échoue **franchement**
  au chargement (`maestro.agents.permissions._ask_validee`, qui nomme les crans
  admis — l'ensemble s'est réduit tout seul, il n'y a aucune liste en dur à tenir
  d'accord) : une politique qu'on est en train de charger peut encore être
  corrigée ;
- **en relecture**, un événement déjà émis qui porte `"orchestrateur"` se relit
  `humain` (`decideur_depuis`, plus bas) : un événement déjà émis, non.

Le repli tolérant envoie donc les anciens événements vers le cran **le plus
fermé**. C'est le sens sûr, il était déjà écrit, et c'est ce qui rend le retrait
sûr sans une ligne de migration. Ne pas l'attendrir « pour compatibilité ».

Et l'invariant d'EF-08 en sort **plus fort** : il n'existe plus aucun canal machine
sur aucun chemin de `Guardrails.demande_validation`. « L'orchestrateur ne peut
jamais approuver un acte classé `humain` » n'a plus besoin d'être tenu par
l'absence d'une branche — il n'y a plus d'orchestrateur. Une propriété qu'on ne
peut pas violer faute de sujet est plus forte qu'une propriété tenue par un
routage correct.

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
    """Qui tranche un acte soumis à arbitrage — deux crans, `HUMAIN` par défaut.

    Valeurs en clair : elles voyagent telles quelles en JSON (fichier de
    politique, journal, API, fil temps réel) et se lisent sans table de
    correspondance. C'est le même parti pris que `Verdict` (#580), avec lequel
    elles ne se confondent pas : le verdict dit **ce qui arrive à l'appel**
    (passe / arbitrage / refus), le décideur dit **qui tranche** quand il est
    arbitré.

    ⚠ Cette énumération est l'**ensemble admissible d'une politique**, pas la
    liste de ce qu'un événement peut porter. Un `"orchestrateur"` déjà consigné
    n'en est plus membre depuis #715 et se relit `humain` (`decideur_depuis`) —
    la donnée durable, elle, ne bouge pas.
    """

    AUTO = "auto"
    HUMAIN = "humain"


#: Le cran d'un acte dont personne n'a précisé qui le tranche. `HUMAIN`, et c'est
#: le critère du ticket : *un cran non précisé escalade, il ne s'auto-approuve
#: pas*. Un défaut à `AUTO` ferait d'un oubli de politique un laissez-passer, ce
#: qui est exactement le défaut symétrique de celui que le parent #573 répare.
DECIDEUR_DEFAUT = Decideur.HUMAIN

#: Le nom sous lequel l'orchestrateur signe ce qu'il demande — l'acteur des
#: allers-retours de clarification du brief (#321,
#: `maestro.controltower.brief.ACTEUR_BRIEF`). Une seule orthographe, parce que
#: deux rendraient illisible la trace de ce qu'un même acteur a fait.
#:
#: ⚠ **Littéral depuis #715, et c'est le prix du retrait, écrit là où le lien
#: vivait.** #586 avait délibérément noué l'acteur au cran de décision —
#: `ACTEUR_ORCHESTRATEUR = str(Decideur.ORCHESTRATEUR)` — avec ce motif : « *c'est
#: le même acteur que le cran de décision `orchestrateur` […] deux constantes
#: littérales laisseraient croire à deux acteurs qui se ressemblent* ». Le membre
#: d'énumération est parti (le cran n'avait aucun canal, docs/31 §3), donc le lien
#: aussi : il n'y a plus de second acteur auquel se comparer, et #586 reste vrai
#: pour l'autre moitié de ce qu'il disait de l'orchestrateur — *il peut répondre à
#: une demande d'information*, ce canal-ci, qui ne lui est pas retiré (répondre à
#: une question n'est pas approuver un acte).
#:
#: ⚠ La **valeur ne bouge pas**, au caractère près : `"orchestrateur"`. C'est ce
#: qui fait qu'il n'y a **aucune migration de donnée** — la renommer en
#: fabriquerait une là où il n'y en a aucune.
ACTEUR_ORCHESTRATEUR = "orchestrateur"


def decideur_depuis(brut: object) -> Decideur:
    """Relit un cran venu du dehors — **`HUMAIN` dès que la lecture échoue**.

    Régime de **relecture**, jamais de saisie : ce qui arrive ici vient d'un
    événement du bus, d'une projection ou du rejeu d'un journal durable — trois
    producteurs dont aucun n'a à faire échouer une demande d'arbitrage parce
    qu'une valeur a vieilli. Même parti pris que `arguments_depuis`
    (`maestro.acte`) et `etapes_depuis` : on relit ce qu'on reconnaît, on retombe
    sur le défaut pour le reste.

    Et le défaut, ici, est le seul qui soit sûr. Une valeur inconnue — un cran
    ajouté par une version plus récente, un cran **retiré** par celle-ci, une
    chaîne tronquée — escalade vers l'humain ; elle ne s'auto-approuve pas. C'est
    le pendant exact de la règle d'écriture, qui elle **refuse** franchement : une
    politique qu'on est en train de charger peut encore être corrigée, un
    événement déjà émis non.

    C'est ce qui absorbe le retrait du cran `orchestrateur` (#715) sans une ligne
    de migration : les événements qui portent cette chaîne se relisent `humain`,
    c'est-à-dire vers le cran **le plus fermé**. ⚠ Ne pas attendrir ce repli « pour
    compatibilité » en faisant retomber l'ancien cran sur `auto` — ce serait
    transformer rétroactivement des demandes d'arbitrage en laissez-passer.
    """
    try:
        return Decideur(str(brut))
    except ValueError:
        return DECIDEUR_DEFAUT
