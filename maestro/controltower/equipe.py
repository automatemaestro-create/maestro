"""L'équipe d'un projet, côté Control Tower : la proposer, et en écrire les playbooks.

La pièce que `POST /api/projets/{id}/equipe/proposition` appelle, au patron de
[`maestro.controltower.outillage`](./outillage.py) : le service tient la forme
JSON et les refus, `app.py` ne fait que les traduire en codes HTTP.

**Une couche mince, et deux choses qu'elle seule peut faire.** Toute la
dérivation vit dans `maestro.equipe`, qui ne connaît ni HTTP, ni projet déclaré,
ni fournisseur de modèle. Ce module n'ajoute que ce qui demande l'un des trois :

1. il **résout le projet** avant de regarder le disque, comme
   `ServiceOutillage` — la route n'analyse jamais un chemin qu'on lui apporte,
   seulement la racine d'un projet déjà déclaré, donc déjà passée par
   `valider_racine` (EF-38) ;
2. il **écrit les playbooks** par la mécanique de #257
   (`GenerateurDefinitionAgent`), seule couche à savoir appeler un modèle.

## Les deux provenances se rejoignent avant d'arriver ici

Un projet **existant** est analysé (#1030), un projet **neuf** a répondu à un
questionnaire (#1031) — et les deux rendent la même paire `Constats` /
`Recommandation`. L'équipe n'a donc pas deux chemins à tenir d'accord : elle en a
un, et la provenance ne survit que dans le fragment `source` qu'on recopie
(docs/38 §4.1), pour qu'on puisse dire six mois plus tard d'où sort cette équipe.

## Un playbook qui n'a pas pu s'écrire ne perd pas l'équipe

La génération de #257 échoue de trois façons — fournisseur injoignable, muet, ou
hors contrat — et elle échoue **par rôle**. Un rôle dont le playbook n'a pas pu
s'écrire garde celui de son **gabarit** (docs/37 §2.1 : la matière n'est pas
perdue) et le **dit** (`playbook_origine`, `playbook_raison`). C'est un arbitrage,
et il se lit ainsi : rendre 502 sur toute l'équipe parce qu'un quota est épuisé
ferait perdre une analyse de projet entière pour un appel modèle, alors qu'un
playbook générique annoncé comme tel reste validable, modifiable et remplaçable
(#1040).

⚠ **Rien n'est créé**, et ce module n'écrit nulle part : il ne tient aucun dépôt
d'agents en écriture, et la seule chose qu'il demande à `ConfigurationAgents` est
la **liste des noms déjà pris** dans ce projet, pour ne pas proposer un nom qui
se heurterait à la validation. La création est #1040.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.store import NOMS_RESERVES
from maestro.controltower.generation_agent import (
    INTENTION_MAX,
    GenerateurDefinitionAgent,
)
from maestro.controltower.projets import ServiceProjets
from maestro.equipe import (
    ORIGINE_PLAYBOOK_GABARIT,
    ORIGINE_PLAYBOOK_GENERE,
    PropositionEquipe,
    RolePropose,
    avec_playbook,
    proposer_equipe,
)
from maestro.outillage import Analyse, Bornes, analyser
from maestro.outillage.modele import Constats, Recommandation
from maestro.outillage.questionnaire import (
    Choix,
    constats_depuis_choix,
    deductions,
    recommandation_depuis_choix,
    source_manifeste_des_choix,
)
from maestro.projets import Projet

#: Ce qu'on écrit sur un rôle dont le playbook a bien été rédigé pour ce projet.
#: Une phrase, parce que la vraie explication est l'`intention` que le rôle
#: porte déjà — et qu'on ne la redit pas deux fois.
RAISON_PLAYBOOK_GENERE = (
    "playbook écrit pour ce projet à partir de l'intention ci-dessous (#257) ; "
    "il se relit et se modifie avant la validation"
)


class ServiceEquipe:
    """L'équipe qu'un projet déclaré appelle — proposée, jamais créée (#1039).

    `gabarits` est la configuration d'agent au niveau des **gabarits**
    (`ConfigurationAgents`, #1038) : elle ne sert qu'à savoir quels noms sont
    déjà pris dans le projet visé, et elle n'est jamais écrite.

    `generateur` est la mécanique de #257. `None` en construit un, résolu
    **paresseusement** comme partout ailleurs dans la Control Tower : construire
    le service ne coûte rien et ne lève aucune erreur de configuration, ce dont
    `create_app` dépend. Les tests en injectent un factice — ou passent
    `playbooks=False` pour s'en tenir aux playbooks des gabarits.

    `bornes` resserre la lecture de l'analyse, comme pour `ServiceOutillage` ;
    `None` laisse le défaut de `maestro.outillage`.
    """

    def __init__(
        self,
        projets: ServiceProjets,
        gabarits: ConfigurationAgents,
        *,
        bornes: Bornes | None = None,
        generateur: GenerateurDefinitionAgent | None = None,
        playbooks: bool = True,
    ) -> None:
        self._projets = projets
        self._gabarits = gabarits
        self._bornes = bornes
        self._generateur = generateur
        self._playbooks = playbooks

    async def proposer(
        self, id_projet: str, choix: Sequence[Choix] = ()
    ) -> dict[str, Any]:
        """L'équipe que ce projet appelle, playbooks compris — **rien n'est créé**.

        Le déroulé, dans cet ordre :

        1. le projet est **résolu** (404/422 motivés) avant qu'aucun disque ne
           soit touché ;
        2. la matière est obtenue — l'**analyse** du projet quand aucun choix
           n'est donné, la mue des **réponses** en constats sinon. Le parcours
           d'un projet réel prend des secondes : il est joué **hors de la boucle
           d'événements**, une route qui la bloquerait figeant les flux SSE des
           autres écrans ;
        3. l'équipe est **dérivée** (fonction pure, `maestro.equipe`) ;
        4. les playbooks sont **écrits** par la mécanique de #257, un appel par
           rôle, tous en même temps.

        Lève les refus **motivés** de ses couches (`ProjetInconnu`,
        `ProjetIllisible`, `RacineRefusee`) — jamais un 500 : un projet qu'on ne
        sait pas lire n'est pas une panne.
        """
        projet = self._projets.entite(id_projet)
        acquis = [*choix, *deductions(choix)] if choix else []
        constats, recommandation, source = (
            await asyncio.to_thread(self._matiere_analysee, projet)
            if not acquis
            else self._matiere_choisie(projet, acquis)
        )
        proposition = proposer_equipe(
            constats,
            recommandation,
            projet_id=projet.id,
            choix=acquis,
            noms_pris=self._noms_pris(projet.id),
            source=source,
        )
        return (await self._avec_playbooks(proposition)).to_dict()

    def _matiere_analysee(
        self, projet: Projet
    ) -> tuple[Constats, Recommandation, dict[str, Any]]:
        """Ce qu'un projet **existant** apprend de lui-même — bloquant, lecteur du disque.

        Le périmètre déclaré du projet s'applique (`Projet.perimetre` retire
        d'office `.env` et `**/secrets/**`, docs/24 §2.5) : proposer une équipe
        ne peut pas lire les deux gisements de secrets d'un dépôt d'utilisateur,
        et ce n'est pas une précaution prise ici mais une propriété de ce qui est
        déclaré.
        """
        analyse: Analyse = analyser(
            projet.racine_chemin,
            projet_id=projet.id,
            perimetre=projet.perimetre,
            bornes=self._bornes,
        )
        return analyse.constats, analyse.recommandation, analyse.source_manifeste()

    def _matiere_choisie(
        self, projet: Projet, acquis: Sequence[Choix]
    ) -> tuple[Constats, Recommandation, dict[str, Any]]:
        """Ce qu'un projet **neuf** doit à ses réponses — aucun fichier n'est ouvert.

        Les mêmes fonctions que `POST …/outillage/recommandation` (#1031), sans
        variante : l'équipe d'un projet neuf branche donc exactement les skills
        que son outillage lui recommandera.
        """
        return (
            constats_depuis_choix(acquis),
            recommandation_depuis_choix(acquis),
            source_manifeste_des_choix(projet.id, acquis),
        )

    def _noms_pris(self, projet_id: str) -> tuple[str, ...]:
        """Les noms qu'une fiche ne peut pas porter dans ce projet.

        Deux gisements : les agents **déjà définis dans le projet**
        (`ConfigurationAgents.pour_projet`, #1038) et les `NOMS_RESERVES` du
        dépôt — les rôles du code et les acteurs système, que `AgentStore`
        refuse. Les demander ici évite qu'une validation (#1040) tombe sur une
        collision dont l'utilisateur n'est pas l'auteur.

        Un dépôt illisible ne fait pas échouer la proposition : au pire un nom
        est proposé déjà pris, et c'est la création qui le dira. Perdre une
        analyse de projet pour un dossier qu'on n'a pas su lister serait un bien
        mauvais échange.
        """
        try:
            definis = self._gabarits.pour_projet(projet_id).agents.noms()
        except (OSError, ValueError):  # pragma: no cover - dépôt illisible
            definis = ()
        return tuple(definis) + tuple(sorted(NOMS_RESERVES))

    async def _avec_playbooks(self, proposition: PropositionEquipe) -> PropositionEquipe:
        """La proposition, chaque playbook écrit pour ce projet quand c'est possible.

        Les rôles sont traités **en même temps** : ce sont trois à cinq appels
        modèle indépendants, et les enchaîner ferait attendre l'utilisateur pour
        rien. Aucun ne peut faire tomber les autres — chacun rattrape son propre
        échec et retombe sur le playbook de son gabarit.
        """
        if not self._playbooks or not proposition.roles:
            return proposition
        rediges = await asyncio.gather(
            *(self._playbook(role) for role in proposition.roles)
        )
        return replace(proposition, roles=tuple(rediges))

    async def _playbook(self, role: RolePropose) -> RolePropose:
        """Le rôle, son playbook écrit par #257 — ou celui de son gabarit, dit comme tel.

        L'intention est **bornée ici**, où la borne vit (`INTENTION_MAX`, #257) :
        la composer est le travail de `maestro.equipe`, la connaître est celui de
        cette couche. Une intention trop longue serait refusée par le générateur,
        et retomber sur le gabarit pour une phrase de trop serait un échec qu'on
        peut éviter en la coupant.

        Toute panne est rattrapée, `GenerationIndisponible` comme le reste : du
        point de vue de qui attend une équipe, un quota épuisé, un réseau coupé
        et un modèle qui répond à côté produisent le même fait — ce rôle garde le
        playbook de son gabarit, et la proposition le dit.
        """
        try:
            propose = await self._generateur_actif().proposer(
                role.intention[:INTENTION_MAX]
            )
        except Exception as exc:  # noqa: BLE001 — toute panne d'appel est la même ici
            return avec_playbook(
                role,
                role.playbook,
                origine=ORIGINE_PLAYBOOK_GABARIT,
                raison=(
                    f"playbook du gabarit « {role.gabarit} » : sa rédaction pour ce "
                    f"projet n'a pas abouti ({exc}). Il reste modifiable, et une "
                    "nouvelle proposition la retentera"
                ),
            )
        return avec_playbook(
            role,
            propose.playbook,
            origine=ORIGINE_PLAYBOOK_GENERE,
            raison=RAISON_PLAYBOOK_GENERE,
        )

    def _generateur_actif(self) -> GenerateurDefinitionAgent:
        """Le générateur de #257, construit au premier usage (jamais au câblage)."""
        if self._generateur is None:
            self._generateur = GenerateurDefinitionAgent()
        return self._generateur
