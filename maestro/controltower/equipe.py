"""L'équipe d'un projet, côté Control Tower : la proposer, puis la **créer**.

La pièce que `POST /api/projets/{id}/equipe/proposition` (#1039) et
`POST /api/projets/{id}/equipe` (#1040) appellent, au patron de
[`maestro.controltower.outillage`](./outillage.py) : le service tient la forme
JSON et les refus, `app.py` ne fait que les traduire en codes HTTP.

**Une couche mince, et trois choses qu'elle seule peut faire.** Toute la
dérivation vit dans `maestro.equipe`, qui ne connaît ni HTTP, ni projet déclaré,
ni fournisseur de modèle. Ce module n'ajoute que ce qui demande l'un des trois :

1. il **résout le projet** avant de regarder le disque, comme
   `ServiceOutillage` — la route n'analyse jamais un chemin qu'on lui apporte,
   seulement la racine d'un projet déjà déclaré, donc déjà passée par
   `valider_racine` (EF-38) ;
2. il **écrit les playbooks** par la mécanique de #257
   (`GenerateurDefinitionAgent`), seule couche à savoir appeler un modèle ;
3. il **écrit l'équipe validée dans le projet** (#1040) — et c'est le seul verbe
   de ce module qui touche un dépôt en écriture, `creer`.

## Proposer et créer sont deux verbes, jamais deux moitiés d'un seul

`proposer` n'écrit rien, `creer` n'analyse rien. Ce n'est pas une coquetterie :
la proposition appelle un modèle pour rédiger les playbooks, si bien que la
rejouer rendrait un **autre** texte. Ce que `creer` reçoit est donc l'équipe
**telle que l'utilisateur l'a lue et ajustée** (`maestro.equipe.creation`), et ce
qui est écrit est exactement ce qui a été montré — la seule façon de tenir la
promesse du ticket sur le cran `auto` : *c'est vous qui le décidez* (#716).

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

⚠ **Une proposition ne crée rien.** `proposer` ne tient aucun dépôt en écriture,
et la seule chose qu'il demande à `ConfigurationAgents` est la **liste des noms
déjà pris** dans ce projet, pour ne pas proposer un nom qui se heurterait à la
validation. L'écriture a un seul point d'entrée, `creer`, et elle vérifie
**toute** l'équipe avant d'écrire le premier fichier
(`maestro.equipe.creation.refus_de`) : sans transaction de système de fichiers,
c'est le seul moyen de ne pas laisser une demi-équipe derrière un refus.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
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
    AgentCree,
    EquipeCreee,
    PropositionEquipe,
    Refus,
    RoleManquant,
    RolePropose,
    RoleValide,
    avec_playbook,
    capacite,
    definition,
    proposer_equipe,
    proposer_renfort,
    refus_de,
    skills_constates,
)
from maestro.outillage import Analyse, Bornes, analyser
from maestro.outillage.modele import Constats, Recommandation
from maestro.outillage.questionnaire import (
    Choix,
    constats_depuis_choix,
    recommandation_depuis_choix,
    source_manifeste_des_choix,
)
from maestro.projets import Projet

#: Ce qu'on écrit sur un rôle dont le playbook a bien été rédigé pour ce projet.
#: Une phrase, parce que la vraie explication est l'`intention` que le rôle
#: porte déjà — et qu'on ne la redit pas deux fois.
RAISON_PLAYBOOK_GENERE = (
    "playbook écrit pour ce projet à partir de l'intention ci-dessous ; "
    "il se relit et se modifie avant la validation"
)


class EquipeRefusee(ValueError):
    """L'équipe validée ne peut pas être créée — et **rien** ne l'a été (#1040).

    Une seule exception pour toutes les causes de `refus_de` (nom pris, doublon,
    instances hors bornes, fiche ou politique que les dépôts refuseraient) :
    ce que l'appelant doit distinguer n'est pas la famille du refus mais **quel
    rôle** l'a provoqué, et `refus` le porte rôle par rôle. La traduction en 422
    motivé est celle des routes projets, dans `app.py`.
    """

    #: Le code stable que les routes projets servent en `{motif, message}` — il
    #: permet à l'écran de reconnaître ce refus-là sans analyser une phrase
    #: (`detail_refus`, docs/05 §2.7).
    motif = "equipe-refusee"

    def __init__(self, refus: Sequence[Refus]) -> None:
        self.refus = tuple(refus)
        detail = " ; ".join(f"{r.nom or '?'} : {r.raison}" for r in self.refus)
        super().__init__(
            f"équipe refusée, aucun agent créé — {detail}" if detail else "équipe refusée"
        )


class ServiceEquipe:
    """L'équipe qu'un projet déclaré appelle : proposée (#1039), puis créée (#1040).

    `gabarits` est la configuration d'agent au niveau des **gabarits**
    (`ConfigurationAgents`, #1038). `proposer` ne lui demande que les noms déjà
    pris ; `creer` la **cadre sur le projet** (`pour_projet`) et écrit dans les
    dépôts de ce projet-là — jamais dans ceux des gabarits, qui n'appartiennent
    à personne.

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
        self,
        id_projet: str,
        choix: Sequence[Choix] = (),
        *,
        renfort: RoleManquant | None = None,
        raison: str = "",
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

        `renfort` (#1227) change la **seule** étape 3 : au lieu de l'équipe que le
        projet appelle, un unique rôle — celui que le plan d'un run demande et que
        l'équipe n'a pas (`proposer_renfort`), justifié par `raison`. Les trois
        autres étapes sont identiques, playbook écrit pour ce projet compris :
        c'est ce qui fait qu'un rôle recruté en cours de run n'est pas une fiche au
        rabais. Un rôle de renfort **n'est justifié par aucun constat** — un
        designer ne l'est pas dans un projet en Python —, et c'est précisément
        pourquoi il ne peut pas sortir de la dérivation ordinaire.

        Lève les refus **motivés** de ses couches (`ProjetInconnu`,
        `ProjetIllisible`, `RacineRefusee`) — jamais un 500 : un projet qu'on ne
        sait pas lire n'est pas une panne. Et `ValueError` sur un renfort dont le
        gabarit n'est pas au catalogue : c'est un 422, pas un rôle inventé.
        """
        projet = self._projets.entite(id_projet)
        # Les réponses d'un projet neuf arrivent avec ce qui en a été compris (#1147) :
        # rien ne se re-déduit ici, et le modèle n'est pas rappelé.
        acquis = list(choix)
        constats, recommandation, source = (
            await asyncio.to_thread(self._matiere_analysee, projet)
            if not acquis
            else self._matiere_choisie(projet, acquis)
        )
        proposition = (
            proposer_renfort(
                constats,
                recommandation,
                renfort,
                raison,
                projet_id=projet.id,
                noms_pris=self._noms_pris(projet.id),
                source=source,
            )
            if renfort is not None
            else proposer_equipe(
                constats,
                recommandation,
                projet_id=projet.id,
                choix=acquis,
                noms_pris=self._noms_pris(projet.id),
                source=source,
            )
        )
        return (await self._avec_playbooks(proposition)).to_dict()

    def creer(
        self,
        id_projet: str,
        roles: Sequence[RoleValide],
        *,
        proposition_id: str = "",
    ) -> dict[str, Any]:
        """Crée dans le projet l'équipe que l'utilisateur a validée (#1040).

        Le déroulé, et l'ordre **est** la garantie :

        1. le projet est **résolu** (404/422 motivés) — un agent ne naît jamais
           dans un projet qu'on ne sait pas lire — et ses rôles ne gardent que
           les skills que **son disque porte** (`skills_constates`, #1212) : un
           playbook ne nomme pas un chemin que l'outillage n'a pas écrit ;
        2. l'équipe entière est **vérifiée** (`refus_de`). Un seul refus arrête
           tout, et **rien n'est écrit** : trois dépôts × N rôles n'offrent
           aucune transaction, et une demi-équipe serait pire qu'un refus ;
        3. chaque rôle est écrit dans les trois dépôts **du projet** — fiche et
           playbook, politique d'autorisations, capacité. La politique n'est
           écrite que si le rôle en porte une : un fichier de politique vide
           *serait* une politique (liste `allow` vide = ouverte), et poser ce
           qu'on n'a pas décidé est ce que ce chantier évite.

        Rend le rapport de `EquipeCreee` : ce qui existe désormais dans le
        projet, à charge pour les écrans d'agents de prendre la suite (#1038).

        Lève `EquipeRefusee` (422 motivé), et les refus de projet de ses couches.
        Synchrone : les trois écritures sont de petits fichiers, et il n'y a
        aucun appel modèle ici — c'est justement ce qui distingue `creer` de
        `proposer`.
        """
        projet = self._projets.entite(id_projet)
        porte = _porte_dans(projet.racine_chemin)
        roles = [skills_constates(role, porte) for role in roles]
        cfg = self._gabarits.pour_projet(projet.id)
        blocages = refus_de(roles, noms_pris=cfg.agents.noms())
        if blocages:
            raise EquipeRefusee(blocages)
        crees = [self._ecrire_role(cfg, role) for role in roles]
        return EquipeCreee(
            projet_id=projet.id,
            proposition_id=proposition_id,
            agents=tuple(crees),
        ).to_dict()

    @staticmethod
    def _ecrire_role(cfg: ConfigurationAgents, role: RoleValide) -> AgentCree:
        """Les trois écritures d'un rôle, dans l'ordre qui laisse le moins de dette.

        La **fiche d'abord** : c'est elle qui fait exister l'agent au catalogue,
        et les deux autres ne sont que des réglages indexés par son nom. Une
        coupure après la fiche laisse un agent aux défauts (une instance, aucune
        politique dédiée) — lisible, modifiable, et que les écrans d'agents
        rattrapent. L'inverse laisserait une capacité et une politique orphelines
        que rien n'affiche.
        """
        fiche = cfg.agents.ecrire(definition(role))
        if role.politique is not None:
            cfg.permissions.ecrire(fiche.nom, role.politique)
        cfg.capacites.ecrire(capacite(role))
        return AgentCree(
            nom=fiche.nom,
            role=fiche.role,
            instances=role.instances,
            gabarit=role.gabarit,
            skills=tuple(skill.nom for skill in role.skills),
            politique=role.politique,
        )

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


def _porte_dans(racine: Path) -> Callable[[str], bool]:
    """Le constat que `skills_constates` demande : ce chemin est-il un fichier **du projet** ?

    Le chemin vient du corps de la requête (l'équipe validée telle que l'écran la
    rapporte) : il est résolu contre la racine, et tout ce qui en sort — `..`,
    chemin absolu, lien qui mène ailleurs — n'est pas porté par le projet, même
    si le fichier existe. Un skill est un `SKILL.md`, donc un **fichier** : un
    dossier du même nom ne suffit pas à ce que l'agent ait quelque chose à lire.
    """
    base = racine.resolve()

    def porte(chemin: str) -> bool:
        cible = (base / chemin).resolve()
        return cible.is_relative_to(base) and cible.is_file()

    return porte
