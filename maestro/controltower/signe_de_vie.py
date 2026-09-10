"""Le **signe de vie** d'une tâche qui travaille (ticket #836, lot 2 de #834).

Pendant un run réel, les vues d'un run — graphe, frise, Kanban — ne bougent
qu'aux **frontières de tâche** : un nœud passe `en_cours`, puis plus rien
pendant douze minutes, puis il passe `terminee`. Le journal, lui, reçoit un
`agent.activite` toutes les 5 à 15 secondes (#479). La donnée existe donc
déjà, et elle est déjà persistée ; ce qui manquait était sa **jointure** vers
les vues, exactement la nature du travail de #355 pour la frise et de #490
pour le graphe.

Ce module ne définit que la forme de cette jointure : **un horodatage et un
libellé court**, ceux du dernier geste de l'agent sur sa tâche. De quoi dire
« ça bouge, à l'instant », et rien de plus — c'est un **attribut** d'un nœud ou
d'un couloir, jamais une entrée de plus dans la frise. La docstring de
`frise.py` dit pourquoi `agent.activite` n'y entre pas en bloc (l'y verser
noierait les trois signaux que #355 demande de distinguer), et cette raison
tient : le signe de vie montre l'activité **sans défaire le tri**.

Où la règle vit, et pourquoi là
-------------------------------

La projection (`ControlTowerState`) reçoit déjà chaque `agent.activite` et
n'en gardait que la dernière activité de l'**agent** (`_applique_activite`).
Elle garde désormais aussi la dernière de la **tâche** (`EtatTache.activite`),
et c'est elle qui décide si ce dernier geste est un signe de vie : **seule une
tâche `en_cours` en porte un**. Une tâche arrêtée — terminée, échouée,
bloquée, réassignée — n'en porte aucun, quel que soit ce que son agent a fait
en dernier : « ça bouge » ne se dit pas d'une chose qui ne bouge plus.

Le graphe et la frise lisent cette décision là où elle est prise et ne la
refont pas : le nœud reçoit le signe avec l'état de sa tâche (`EtatNoeud`), le
couloir le reçoit par agent (`ControlTowerState.signes_de_vie_du_run`). Deux
vues, **une** règle, et un même geste d'agent rend la même valeur des deux
côtés — le critère « le même signe de vie » tient par construction, pas par
deux calculs à tenir d'accord.

Rien n'est ajouté au canal : le signe se **recompose à la lecture**, comme le
graphe et la frise eux-mêmes. Le `agent.activite` qui le rafraîchit circule
déjà, et c'est lui qui fait battre le pouls du client.

Pourquoi `en_cours` au sens strict, et non le compartiment
----------------------------------------------------------

La table partagée de `progression.py` range `en_attente_validation` avec
`en_cours` — juste pour une barre de progression, où la tâche est en vol. Ici
ce serait faux : une tâche arrêtée sur un humain ne **travaille** pas, et c'est
précisément la distinction que #355 a fait exister. Un signe de vie sur une
attente de décision redirait « ça bouge » là où la vérité est « ça attend ».
Le moteur n'émet d'ailleurs jamais ce statut sur une carte ; la règle est
écrite pour le jour où il le fera.

Module **feuille** : il ne connaît ni la projection, ni FastAPI. Il emprunte à
`events.py` la forme d'un événement, et c'est tout.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from maestro.controltower.events import Event

#: Longueur maximale du libellé, en caractères. Un signe de vie se lit d'un
#: coup d'œil sur une boîte de graphe ou l'en-tête d'un couloir : la première
#: ligne du geste, tronquée — le journal requêtable rend le reste. Quatre-vingts
#: est la largeur d'une ligne de titre, pas une mesure : le point est qu'il y
#: en ait une, et qu'elle soit la même partout.
LARGEUR_LIBELLE = 80

#: Ce qui termine un libellé tronqué. Un seul caractère, pour que la longueur
#: rendue ne dépasse jamais `LARGEUR_LIBELLE`.
ELLIPSE = "…"


def libelle_court(texte: str, largeur: int = LARGEUR_LIBELLE) -> str:
    """La première ligne non vide de `texte`, blancs repliés, bornée à `largeur`.

    Un `agent.activite` porte la **salve** publiée par le fournisseur (#479) :
    plusieurs lignes, parfois longues. Le signe de vie n'en garde que de quoi
    reconnaître le geste. La troncature se dit (`…`) — une ligne coupée en
    silence se lirait comme la phrase entière.
    """
    for ligne in texte.splitlines():
        mots = ligne.split()
        if not mots:
            continue
        libelle = " ".join(mots)
        if largeur >= 0 and len(libelle) > largeur:
            coupe = max(largeur - len(ELLIPSE), 0)
            return libelle[:coupe].rstrip() + ELLIPSE
        return libelle
    return ""


@dataclass(frozen=True)
class SigneDeVie:
    """Ce qu'une vue montre d'une tâche qui travaille : son dernier geste, et son âge.

    `horodatage` est celui de l'événement qui a produit le geste, tel quel —
    c'est lui qu'une vue rend en « il y a 12 s », et lui qui prouve que deux
    lectures espacées d'un geste ne rendent pas la même chose. `libelle` est le
    geste, abrégé par `libelle_court` ; vide si l'événement n'en disait rien, et
    l'horodatage suffit alors à dire « vivant ».

    Deux temps, et pas un (#894)
    ----------------------------

    `travaille_depuis` est l'instant où la **tâche** a commencé à travailler —
    vide tant qu'on ne le sait pas. Les deux ne disent pas la même chose, et le
    front porte déjà les deux mots avec leur raison (`lib/format.ts`) :
    l'horodatage du geste **situe un fait passé** (« il y a 12 s » — *ça
    bouge*), l'instant de départ **mesure une attente qui dure** (« depuis
    6 min » — *ça dure*). Seul le second distingue une tâche vivante d'une tâche
    vivante mais **partie trop loin** : celle qui enchaîne des gestes depuis
    vingt minutes sur un sujet qui en demandait deux.

    Ils voyagent **ensemble** plutôt que côte à côte, et c'est le point : le
    couloir d'un agent multi-instances (#100) retient le signe de la plus
    récente de ses tâches en cours (`plus_recent_que`), si bien qu'un second
    champ transporté à part montrerait le geste d'une tâche et l'ancienneté
    d'une autre. Une valeur, une tâche.

    La comparaison, elle, ne porte **que** sur l'horodatage du geste : c'est lui
    qui dit lequel de deux signes est le plus frais. Le départ d'une tâche ne
    bouge pas.
    """

    horodatage: str
    libelle: str = ""
    travaille_depuis: str = ""

    @classmethod
    def depuis(cls, event: Event) -> SigneDeVie:
        """Le signe de vie que porte `event` — son instant et son détail abrégé.

        Sans `travaille_depuis` : un événement dit ce que l'agent vient de
        faire, jamais depuis quand sa tâche travaille. Ce second temps est posé
        par la projection, qui seule tient le départ de la tâche
        (`EtatTache.debut`), au moment où elle tranche qu'il y a un signe.
        """
        return cls(horodatage=event.horodatage, libelle=libelle_court(event.detail))

    def avec_debut(self, debut: str) -> SigneDeVie:
        """Copie du signe, augmentée de l'instant où la tâche a commencé à travailler.

        Le geste reste ce qu'il était : ce verbe **compose**, il ne recalcule
        rien. Un `debut` vide laisse le champ vide, ce que `to_dict` rend `null`
        — inconnu n'est pas « depuis toujours ».
        """
        return replace(self, travaille_depuis=debut)

    def plus_recent_que(self, autre: SigneDeVie | None) -> bool:
        """Ce signe est-il postérieur à `autre` (ou `autre` absent) ?

        Comparaison lexicale des horodatages, tous produits par `Event` au
        même format UTC ISO-8601 — la règle du journal requêtable (#478), qui
        vaut ici mot pour mot. À instant égal, le premier reste : les
        horodatages sont à la seconde, et un signe qui changerait de tâche
        d'une lecture à l'autre sans qu'aucun geste ait eu lieu ferait sauter
        le libellé sous les yeux.
        """
        return autre is None or self.horodatage > autre.horodatage

    def to_dict(self) -> dict[str, Any]:
        """La forme JSON d'un signe de vie (`SigneDeVie`, apps/web/lib/types.ts).

        `travaille_depuis` sort à `null` quand il est vide — le client distingue
        ainsi « on ne sait pas depuis quand » d'un départ connu, et n'affiche
        alors que le premier temps, comme avant #894.
        """
        return {
            "horodatage": self.horodatage,
            "libelle": self.libelle,
            "travaille_depuis": self.travaille_depuis or None,
        }
