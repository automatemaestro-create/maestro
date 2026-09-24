"""Où l'outillage s'écrit : **le régime du projet**, jamais un choix de ce module (#1033).

[docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md) a tranché une
fois pour toutes comment Maestro écrit dans le dossier de quelqu'un, et la
génération d'outillage n'y fait pas exception — c'est un travail sur le projet
comme un autre :

| Projet | Où la génération écrit | Ce qui atteint la racine |
| --- | --- | --- |
| **versionné** | un **worktree** hors de la racine | la branche, **fusionnée sous accord** |
| **non versionné** | **la racine**, en place (#839) | ce qui s'écrit, pendant que ça s'écrit |

Le second n'a pas d'accord, et c'est la décision de #839 : il n'y a pas de moment
de fusion où l'accrocher.

Ce module fait **la première colonne**, et elle seule : il prépare, il n'applique
pas. L'accord et la fusion restent là où ils vivent
(`maestro.controltower.validation.appliquer_sous_validation`), pour la raison qui
a fait poser cette frontière en #227 : un module qui écrit dans un projet ne doit
pas être celui qui décide qu'on a le droit d'y écrire. C'est l'appelant — le
service de la Control Tower — qui marie les deux, exactement comme
`maestro.engine.executor` le fait pour le travail d'une tâche.

    preparation = generer_outillage(
        projet, analyse.constats, analyse.recommandation, source=analyse.source_manifeste()
    )
    preparation.regime          # "en-place" : c'est fait, la racine porte l'outillage
    preparation.branche         # "branche" : `maestro/outillage-…` attend la fusion
    preparation.rapport         # ce qui a été écrit, refusé, ignoré — dans les deux cas

**Le rapport est le même dans les deux régimes**, et c'est voulu : le manifeste
lu est celui de l'arbre où l'on écrit, donc un worktree — copie conforme de la
branche de base — rend les mêmes verdicts que la racine. Une personne qui relit
« `AGENTS.md` refusé, il a été modifié » n'a pas à savoir dans quel régime son
projet est.

**Rien n'est jeté nulle part.** Sur un projet versionné, l'accord refusé ou jamais
rendu laisse la branche intacte (`maestro.sandbox.projet` ne supprime jamais une
branche) : l'outillage proposé reste consultable et se récupère d'un `git merge`.
Sur un projet non versionné, ce qui est écrit est écrit — et ce qui garde est
alors ce qui garde déjà les agents : la **frontière d'écriture**, le manifeste
qui refuse d'écraser, et le rapport qui nomme chaque geste.

**Chaque commande est jouée avant d'être écrite** (#1160). Entre la lecture du
manifeste de la cible et la rédaction, le `Verificateur`
(`maestro.outillage.verification`) joue dans une **copie** de la cible les commandes
que la rédaction va écrire, et leurs verdicts entrent dans le texte, dans le
manifeste et dans le rapport. C'est ici et pas plus tôt parce que c'est le seul
moment où l'on sait **quel arbre** sera outillé — le worktree frais d'un projet
versionné, la racine d'un projet non versionné —, et parce que l'analyse, elle,
n'exécute rien (docs/38).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maestro.outillage.generation import Rapport, generer, portees_declarees
from maestro.outillage.modele import Constats, Recommandation
from maestro.outillage.redaction import rediger
from maestro.outillage.verification import Verificateur
from maestro.projets.modele import Projet
from maestro.projets.racine import valider_racine
from maestro.sandbox.en_place import FrontiereEcriture
from maestro.sandbox.projet import branche_de_tache, espace_de_travail

#: Le régime d'écriture, tel qu'il se lit dans la réponse. `en-place` : c'est
#: fait. `branche` : l'outillage est sur une branche et attend la fusion — deux
#: situations qu'un appelant ne doit pas confondre, parce que la seconde demande
#: encore quelque chose à quelqu'un.
REGIME_EN_PLACE = "en-place"
REGIME_BRANCHE = "branche"

#: Préfixe de l'identifiant de « tâche » d'une génération. Il donne son nom à la
#: branche (`maestro/outillage-3c9f…`) et il ne se réduit **jamais** à `outillage`
#: nu : ce nom-là est réservé à la comptabilité de Maestro sous `.maestro/`
#: (docs/38 §4.3, `maestro.sandbox.en_place.ATELIERS_RESERVES`).
PREFIXE_TACHE = "outillage-"


@dataclass(frozen=True)
class Preparation:
    """L'outillage écrit — et, s'il attend encore, ce qui le porte.

    `branche` est vide en régime en place : il n'y a rien à fusionner, la racine
    porte déjà ce que le rapport décrit. Elle est renseignée en régime branche,
    et c'est elle que l'appelant passe à `appliquer_sous_validation` — jamais
    recalculée ailleurs (`maestro.sandbox.projet.branche_de_tache` en est la
    seule orthographe).
    """

    regime: str
    rapport: Rapport
    branche: str = ""
    tache_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """La préparation en JSON."""
        return {
            "regime": self.regime,
            "branche": self.branche,
            "tache_id": self.tache_id,
            "rapport": self.rapport.to_dict(),
        }


def nouvel_id_de_generation() -> str:
    """L'identifiant d'une génération — `outillage-<8 hex>`, jamais `outillage` nu."""
    return f"{PREFIXE_TACHE}{uuid.uuid4().hex[:8]}"


def generer_outillage(
    projet: Projet,
    constats: Constats,
    recommandation: Recommandation,
    *,
    source: Mapping[str, Any],
    tache_id: str = "",
    horodatage: str = "",
    verificateur: Verificateur | None = None,
) -> Preparation:
    """Écrit l'outillage que `recommandation` retient dans `projet`, au régime de docs/24 §2.4.

    La **rédaction a lieu ici**, et pas chez l'appelant : le texte dépend de ce
    que le manifeste de la cible déclare déjà (`portees_declarees` →
    `rediger(portees=…)`), donc seul celui qui connaît la cible peut le rendre.
    Confier la rédaction à l'appelant reviendrait à lui faire deviner dans quel
    arbre on va écrire, et une régénération y perdrait l'idempotence — c'est le
    défaut que le banc du 2026-09-20 a montré.

    **Bloquant** : parcours du disque, sous-processus Git sur un projet versionné,
    et les commandes du projet jouées dans leur copie de vérification — jusqu'aux
    délais de `verificateur` (`Delais`). Un appelant asynchrone le joue hors de sa
    boucle d'événements.

    `verificateur` joue les commandes avant qu'elles ne soient écrites (#1160).
    `None` vaut le vérificateur **réel** : il n'y a pas de génération sans
    vérification, et c'est la suite de tests qui neutralise l'exécution d'un seul
    endroit (`tests/conftest.py`), jamais un appelant qui l'oublierait.

    La racine est **revalidée** (`valider_racine`, EF-38) et pas seulement à la
    déclaration : le dépôt des projets est un dossier de fichiers JSON qu'on peut
    éditer à la main, et c'est ici la dernière porte avant d'écrire dans le
    dossier de quelqu'un.

    Lève `RacineRefusee` si la racine n'est plus admissible et
    `EspaceProjetIndisponible` si le worktree d'un projet versionné ne se monte
    pas — deux refus **motivés**, que l'appelant traduit. Tout le reste (chemin
    refusé, fichier modifié à la main, disque en écriture seule) est une ligne du
    rapport, jamais une exception : il ne doit pas y avoir d'état où une partie de
    l'outillage est posée et où personne ne sait laquelle.
    """
    racine = valider_racine(projet.racine)
    verifie = verificateur if verificateur is not None else Verificateur()
    if not projet.versionne:
        return Preparation(
            regime=REGIME_EN_PLACE,
            rapport=_ecrire(
                racine, projet, constats, recommandation, source, horodatage, verifie
            ),
        )

    tache = tache_id or nouvel_id_de_generation()
    # Le worktree est monté, écrit, puis **démonté** : la sortie du bloc commite
    # ce qui y a été posé sur `maestro/<tâche>` (`_solder_la_branche`) et retire
    # l'arbre, jamais la branche. C'est elle qui porte l'outillage jusqu'à la
    # fusion — exactement le déroulé d'une tâche soldée (#705), et c'est pourquoi
    # rien de Git n'est réécrit ici.
    with espace_de_travail(projet, tache_id=tache) as espace:
        rapport = _ecrire(
            espace.path, projet, constats, recommandation, source, horodatage, verifie
        )
    return Preparation(
        regime=REGIME_BRANCHE,
        rapport=rapport,
        branche=branche_de_tache(tache),
        tache_id=tache,
    )


def _ecrire(
    cible: Path,
    projet: Projet,
    constats: Constats,
    recommandation: Recommandation,
    source: Mapping[str, Any],
    horodatage: str,
    verificateur: Verificateur,
) -> Rapport:
    """Vérifie, rédige contre le manifeste de `cible`, puis écrit — même geste, deux régimes.

    Les commandes sont jouées **avant** la rédaction, dans une copie de `cible`
    (#1160) : leurs verdicts font partie du texte, donc ils doivent être connus
    quand on le rend. La copie est faite avant toute écriture — ce qu'on vérifie
    est le projet, pas l'outillage qu'on s'apprête à y poser.

    La **frontière d'écriture** est armée sur la cible et le périmètre du projet
    (`maestro.sandbox.en_place`, #839) : hors de l'arbre, à travers un lien
    symbolique ou sur un chemin exclu, l'écriture est refusée avec son motif. La
    même qu'un agent rencontre en place, jamais une seconde — deux orthographes
    de « où a-t-on le droit d'écrire » auraient fini par ne pas refuser les mêmes
    chemins.
    """
    portees = portees_declarees(cible)
    verifications = verificateur.verifier(
        cible, constats, recommandation, perimetre=projet.perimetre, portees=portees
    )
    fichiers = rediger(
        constats,
        recommandation,
        portees=portees,
        source=source,
        verifications=verifications,
    )
    return generer(
        cible,
        fichiers,
        source=source,
        frontiere=FrontiereEcriture.pour(cible, projet.perimetre),
        horodatage=horodatage,
        verifications=verifications,
    )
