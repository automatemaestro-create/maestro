"""Ce que l'équipe d'un projet reçoit de son outillage — tenu, puis constaté (#1520).

Une commande de l'outillage se **remplace** après le recrutement de l'équipe : la
personne la corrige avec ses mots (`maestro.outillage.correction`), la revue d'après un
run en propose une autre, lue dans le projet construit (#1381). Le remplacement
réécrit les pièces sur accord et le manifeste suit — mais chaque playbook de l'équipe
listait, depuis sa création, la commande que chacun de ses skills « enveloppait ».
Relevé au bouclage du 2026-10-14 (S9, passage `20261014-171347` ; « herbier-des-cimes ») :
les playbooks gardaient l'ancienne commande, un agent du run suivant en recevait deux
pour le même geste, et Maestro affirmait à la personne que tout suivait.

Deux gestes, joués par `ServicePieces.ecrire` après chaque pièce écrite, et c'est
l'ordre qui compte :

1. **tenir** — `rebrancher` : le playbook de chaque agent du projet perd les commandes
   que sa section des skills figeait (`maestro.equipe.creation.sans_commandes_figees`).
   La section n'en fige plus depuis #1520 : la commande vit dans le skill, qui fait foi,
   et que la correction réécrit. Ce geste ne sert donc que les équipes recrutées avant ;
   il est muet sur les autres ;
2. **constater** — `remplacees` : ce que l'écriture remplace, et où l'équipe le reçoit
   **encore**, relu sur le disque après coup — un skill que la correction touche mais qui
   n'est pas encore réécrit, le corps d'un playbook qu'une personne a écrit. C'est ce
   constat que le fait de l'écriture porte (`PieceEcrite.recu_en_phrase`), et c'est de lui
   que le fil parle : Maestro ne dit qu'une commande vaut « partout » que s'il l'a vu.

Ce module ne juge aucun texte : il reconnaît la forme que **Maestro** écrit — sa section
des skills, une commande prescrite telle que la rédaction la prescrit (`prescrit`) —, et
rien de ce qu'un modèle ou une personne aurait reformulé.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from maestro.agents.configuration import ConfigurationAgents
from maestro.controltower.chat import CommandeRemplacee
from maestro.equipe.creation import sans_commandes_figees
from maestro.outillage.correction import CorrectionPrise
from maestro.outillage.generation import prescrites_dans
from maestro.outillage.modele import USAGES
from maestro.outillage.redaction import prescrit
from maestro.outillage.verification import Verification


@dataclass(frozen=True)
class Rebranchement:
    """Ce que `rebrancher` a fait à l'équipe d'un projet.

    `rebranches` sont les agents dont un playbook a cessé de figer ses commandes ;
    `recus`, pour **chaque** agent du projet, le playbook que le moteur charge après
    coup — la matière du constat.
    """

    rebranches: tuple[str, ...] = ()
    recus: Mapping[str, str] = field(default_factory=dict)


def rebrancher(equipe: ConfigurationAgents, projet_id: str) -> Rebranchement:
    """Chaque agent du projet, son playbook privé des commandes que sa section figeait — bloquant.

    Deux endroits portent le playbook d'un agent de projet, et le moteur charge le
    premier qui existe (`LocalExecutor._playbook_courant`) : la version publiée — depuis
    l'écran des playbooks, ou une proposition appliquée (#111) —, sinon celui de sa fiche
    (`AgentDefinition.playbook`, écrit à la création). Les deux se rebranchent : la fiche
    parce qu'une version retirée y ramènerait l'agent. Une version publiée se rebranche
    en **publiant** la suivante — le dépôt est append-only, la précédente reste dans
    l'historique et se restaure.

    Rien n'est écrit pour un playbook qui ne fige rien. Une écriture empêchée (dépôt
    illisible, fiche que le dépôt refuse) laisse l'agent tel qu'il était, et ne fait pas
    tomber les autres : le constat qui suit relit ce que chacun reçoit, et le dira.
    """
    cfg = equipe.pour_projet(projet_id)
    try:
        fiches = cfg.agents.lister()
    except (OSError, ValueError):
        return Rebranchement()
    rebranches: list[str] = []
    recus: dict[str, str] = {}
    for fiche in fiches:
        recu, change = fiche.playbook, False
        libere = sans_commandes_figees(fiche.playbook)
        if libere != fiche.playbook:
            try:
                cfg.agents.ecrire(replace(fiche, playbook=libere))
                recu, change = libere, True
            except (OSError, ValueError):
                pass
        try:
            publie = cfg.playbooks.lire(fiche.nom)
        except (OSError, ValueError):
            publie = None
        if publie is not None:
            recu = publie.contenu
            libere = sans_commandes_figees(publie.contenu)
            if libere != publie.contenu:
                try:
                    cfg.playbooks.ecrire(fiche.nom, libere)
                    recu, change = libere, True
                except (OSError, ValueError):
                    pass
        recus[fiche.nom] = recu
        if change:
            rebranches.append(fiche.nom)
    return Rebranchement(tuple(rebranches), recus)


def remplacees(
    racine: Path,
    avant: Sequence[Verification],
    corrections: Sequence[CorrectionPrise],
    recus: Mapping[str, str],
) -> tuple[CommandeRemplacee, ...]:
    """Les commandes que l'écriture remplace, et où l'équipe les reçoit encore — bloquant.

    Remplacée : une commande que le manifeste prescrivait **avant** l'écriture (`avant`,
    ses verdicts), pour un usage qu'une correction retenue a changé — dite par la personne
    ou proposée après un run —, et qui n'est pas celle de la correction. Une correction
    retire la commande de son usage (`_commandes_corrigees`) : toute autre commande de cet
    usage est donc l'ancienne, y compris quand la correction dit « aucun ».

    « Encore » se constate **après** l'écriture, sur ce que les agents reçoivent : les
    fichiers de l'outillage (`prescrites_dans`, la règle même du manifeste) et le
    playbook que le moteur charge pour chacun (`recus`). Vide : plus rien ne la leur donne.
    """
    dites = {c.cle: c.valeur for c in corrections if c.cle in USAGES and c.valeur}
    anciennes = tuple(
        dict.fromkeys(
            v.commande for v in avant if v.usage in dites and v.commande != dites[v.usage]
        )
    )
    if not anciennes:
        return ()
    fichiers = prescrites_dans(racine, anciennes)
    return tuple(
        CommandeRemplacee(
            commande,
            fichiers[commande]
            + tuple(
                f"le playbook de {nom}"
                for nom, texte in recus.items()
                if prescrit(texte, commande)
            ),
        )
        for commande in anciennes
    )
